"""Shared candidate evaluation for generation, resume and standalone debugging."""
from .policy import validate


def evaluate(assertion, source, entrypoint, evaluator, mutants, config):
    if not assertion:
        return {'status': 'fail', 'reason': 'No valid assertion generated'}, None
    try:
        validate(assertion, source, entrypoint, config.get('assertion_policy', 'legacy'))
    except (SyntaxError, ValueError) as exc:
        return {'status': 'fail', 'reason': str(exc), 'stage': 'validation'}, None
    correctness = evaluator.correctness(assertion)
    mutation = evaluator.mutation(assertion, mutants) if correctness['status'] == 'pass' and mutants else None
    return correctness, mutation


def complete(attempt, mutants):
    status = attempt['correctness']['status']
    if status == 'fail':
        return True
    if status != 'pass':
        return False
    if not mutants:
        return True
    fb = (attempt.get('mutation') or {}).get('metrics', {}).get('feedback', {})
    return fb.get('unknown') == 0


def refresh(state, mutants):
    submissions = [a for a in state['attempts'] if a['action'] == 'submit' and a['correctness']['status'] == 'pass']
    def score(a):
        value = (a.get('mutation') or {}).get('metrics', {}).get('feedback', {}).get('lower_bound')
        return value if value is not None else -1
    state['best'] = max(submissions, key=score) if submissions else None
    state['latest'] = state['attempts'][-1] if state['attempts'] else None
    state['history_evaluation_complete'] = all(complete(a, mutants) for a in state['attempts'])
    selected = state['best'] or next((a for a in reversed(state['attempts'])
                                      if a['action'] == 'submit'), state['latest'])
    state['evaluation_complete'] = selected is not None and complete(selected, mutants)
    state['done'] = state.get('generation_done', False) and state['evaluation_complete']
