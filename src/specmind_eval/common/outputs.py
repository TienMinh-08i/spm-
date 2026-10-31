"""Guards for separate output directories."""
from pathlib import Path

def separate_output(output, *sources):
    output = Path(output).resolve()
    for source in sources:
        source = Path(source).resolve()
        if output == source or output in source.parents or source in output.parents:
            raise ValueError('Output must be separate from source run, suite and checkout')
    return output

