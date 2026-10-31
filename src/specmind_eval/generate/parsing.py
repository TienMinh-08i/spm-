import re


def double_check_postcondition(postcondition: str, entrypoint: str) -> str:
    """
    Extracts the postcondition logic from a potentially larger block of text.

    This function handles cases where the LLM might have included boilerplate code,
    like the `return_value` assignment. It ensures that only the final assertion
    and any necessary imports are returned.

    Args:
        postcondition: The raw postcondition text from the LLM.
        entrypoint: The name of the function being tested.

    Returns:
        A cleaned-up postcondition string.
    """
    lines = postcondition.strip().splitlines()
    result_lines = []
    assignment_index = None

    # Find the line where `return_value` is assigned
    for i, line in enumerate(lines):
        if re.match(rf"\s*return_value\s*=\s*{re.escape(entrypoint)}\s*\(.*\)", line):
            assignment_index = i
            break

    if assignment_index is None:
        return postcondition.strip()

    # Keep necessary imports from above the assignment line
    for line in lines[:assignment_index]:
        if re.match(r"\s*(import|from)\s+\S+", line):
            result_lines.append(line)

    # Append everything after the assignment line
    result_lines.extend(lines[assignment_index + 1 :])

    return "\n".join(result_lines).strip()


def parse_model_response(response: str):
    """
    Parses a model's response to extract content from specific tags.

    Args:
        response: The raw string response from the language model.

    Returns:
        A tuple of (think, assertions, solution) strings.
    """
    think_match = re.search(r"<think>(.*?)</think>", response, re.DOTALL)
    assert_match = re.search(r"<assert>(.*?)</assert>", response, re.DOTALL)
    solution_match = re.search(r"<solution>(.*?)</solution>", response, re.DOTALL)

    think_content = think_match.group(1).strip() if think_match else None
    assertion_content = assert_match.group(1).strip() if assert_match else None
    solution_content = solution_match.group(1).strip() if solution_match else None

    # Handle cases where the model stops mid-tag
    if not assert_match and response.strip().endswith("<assert>"):
        incomplete_assert_match = re.search(r"<assert>(.*)", response.strip(), re.DOTALL)
        if incomplete_assert_match:
            assertion_content = incomplete_assert_match.group(1).strip()

    if assertion_content is None and solution_content is None and "assert " in response:
        assertion_content = response.strip()

    return think_content, assertion_content, solution_content
