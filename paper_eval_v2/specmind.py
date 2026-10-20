"""Explore/submit loop with a durable history and independent evaluation states."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter

from .feedback import build_feedback
from .parsing import double_check_postcondition, parse_model_response
from .storage import read, write
from .candidate import evaluate, complete, refresh

SYSTEM = "You are a programming assistant that generates executable python only. You generate correct code, so you only generate code you are sure of. You have Python comments explaining your intent when possible."


def request_llm(messages, config):
    import openai

    key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    base = os.getenv("OPENAI_BASE_URL")
    if not base and os.getenv("OPENROUTER_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        base = "https://openrouter.ai/api/v1"
    client = openai.OpenAI(
        api_key=key, base_url=base, timeout=config["api_timeout"] or None, max_retries=config["api_retries"]
    )
    kwargs = {"model": config["model"], "messages": messages, "temperature": config["temperature"]}
    token_key = (
        "max_completion_tokens"
        if (not base or base.rstrip("/") == "https://api.openai.com/v1")
        and config["model"].startswith("gpt-5")
        else "max_tokens"
    )
    kwargs[token_key] = config["max_tokens"]
    if token_key != "max_completion_tokens":
        kwargs["stop"] = ["</assert>", "</solution>"]
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or "", response.usage.model_dump() if response.usage else {}


def generate(source, entrypoint, evaluator, mutants, config, path, call=request_llm, repository_context=""):
    prompt = Path(__file__).with_name("prompts").joinpath(config["mode"] + ".txt").read_text(encoding="utf-8")
    # Original templates contain ordinary Python braces; replace named fields only.
    for key, value in {
        "function_code": source,
        "codeStubAndDocstring": source,
        "canonical_solution": "",
        "entrypoint": entrypoint,
        "max_turns": config["max_turns"],
    }.items():
        prompt = prompt.replace("{" + key + "}", str(value))
    if repository_context:
        prompt = prompt.replace(
            "based solely on its **docstring** and **implementation**",
            "based on its **docstring**, **implementation**, and supplied repository evidence",
        )
    if repository_context:
        prompt += "\n\n" + repository_context
    if config.get('assertion_policy') == 'behavioral':
        prompt += (
            '\n\nBehavioral assertion policy: use input parameters and return_value only. '
            'Do not reference or call the target function, inspect its signature/defaults, '
            'use arbitrary globals, or mutate inputs. Use one final unconditional assert. '
            'Pure local assignments and approved standard-library imports are allowed '
            '(inspect generator predicates, math numeric predicates, re matching, collections.abc types). '
            'Policy violations receive feedback and consume one attempt.'
        )
    state = read(
        path,
        {
            "history": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "attempts": [],
            "best": None,
            "latest": None,
            "done": False,
        },
    )
    state.setdefault("generation_done", state["done"])
    refresh(state, mutants)
    if state['done']:
        write(path, state)
        return state
    # Replay only unresolved evaluations, using already paid/stored responses.
    for index, attempt in enumerate(state["attempts"]):
        if (not complete(attempt, mutants)
                and attempt.get('evaluation_retries', 0) < config.get('evaluation_retry_limit', 2)):
            previous_feedback = attempt['feedback']
            attempt.setdefault('initial_evaluation', {
                'correctness': attempt['correctness'], 'mutation': attempt.get('mutation'),
                'feedback': previous_feedback})
            attempt["correctness"], attempt["mutation"] = evaluate(
                attempt["assertion"], source, entrypoint, evaluator, mutants, config)
            attempt["evaluation_retries"] = attempt.get("evaluation_retries", 0) + 1
            attempt["feedback"] = build_feedback(attempt["correctness"], attempt["mutation"])
            update = {'turn': attempt['turn'], 'retry': attempt['evaluation_retries'],
                      'feedback': attempt['feedback']}
            state.setdefault('evaluation_updates', []).append(update)
            if not state['generation_done']:
                state['history'].append({'role': 'user', 'content':
                    f"<observation>Reevaluation of attempt {attempt['turn']}:\n" + attempt['feedback'] + '\n</observation>'})
            write(path, state)
    refresh(state, mutants)
    best = state['best']
    if best and (not mutants or (best.get('mutation') and
            best['mutation']['metrics']['feedback']['lower_bound'] >= config['threshold'])):
        state['generation_done'] = True
        state['stop_reason'] = 'correctness_only_submission' if not mutants else 'threshold_reached'
        refresh(state, mutants)
    if state["generation_done"]:
        write(path, state)
        return state
    for turn in range(len(state["attempts"]) + 1, config["max_turns"] + 1):
        started = perf_counter()
        try:
            # Persist paid model output before evaluation, so interrupted evaluation can resume it.
            pending = read(path.with_name("pending_response.json"))
            if pending and pending["turn"] == turn:
                raw, usage = pending["raw"], pending["usage"]
            elif config.get("mock_assertion") is not None:
                raw, usage = "<solution>\n" + config["mock_assertion"] + "\n</solution>", {}
            else:
                raw, usage = call(state["history"], config)
            write(path.with_name("pending_response.json"), {"turn": turn, "raw": raw, "usage": usage})
        except Exception as exc:
            state["error"] = f"{type(exc).__name__}: {exc}"
            write(path, state)
            return state
        llm_seconds = perf_counter() - started
        response_for_parsing = raw
        for tag in ("assert", "solution"):
            if "<" + tag + ">" in response_for_parsing and "</" + tag + ">" not in response_for_parsing:
                response_for_parsing += "\n</" + tag + ">"
        _, exploration, submission = parse_model_response(response_for_parsing)
        candidate = submission or exploration
        action = "submit" if submission or config["mode"] == "retry" else "explore"
        cleaned = double_check_postcondition(candidate, entrypoint) if candidate else None
        correctness, mutation = evaluate(cleaned, source, entrypoint, evaluator, mutants, config)
        feedback = build_feedback(correctness, mutation)
        attempt = {
            "turn": turn,
            "action": action,
            "raw_response": raw,
            "assertion": cleaned,
            "correctness": correctness,
            "mutation": mutation,
            "feedback": feedback,
            "usage": usage,
            "llm_seconds": llm_seconds,
            "seconds": perf_counter() - started,
        }
        state["attempts"].append(attempt)
        state["latest"] = attempt
        state["history"].extend(
            [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": "<observation>\n"
                    + feedback
                    + f"\nTurns remaining: {config['max_turns'] - turn}. Submit a <solution> on the final turn.\n</observation>",
                },
            ]
        )
        if action == "submit" and correctness["status"] == "pass":
            if not mutants:
                state['generation_done'] = True
                state['stop_reason'] = 'correctness_only_submission'
            score = mutation["metrics"]["feedback"]["lower_bound"] if mutation else None
            best = state["best"]
            previous = (
                best["mutation"]["metrics"]["feedback"]["lower_bound"] if best and best["mutation"] else None
            )
            if best is None or (score is not None and (previous is None or score > previous)):
                state["best"] = attempt
            if score is not None and score >= config["threshold"]:
                state["generation_done"] = True
                state["stop_reason"] = "threshold_reached"
        if config.get("mock_assertion") is not None:
            state["generation_done"] = True
            state["stop_reason"] = "mock_assertion_evaluated"
        if turn == config["max_turns"]:
            state["generation_done"] = True
            state.setdefault("stop_reason", "max_turns")
        state.pop("error", None)
        refresh(state, mutants)
        write(path, state)
        # One pass per invocation: unknowns wait for --resume, not a busy retry loop.
        if state["generation_done"]:
            break
    return state
