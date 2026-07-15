"""Optional host adapters around the existing exam-prep command core."""

from .command_core import CommandCoreError, run_json_command

__all__ = ["CommandCoreError", "run_json_command"]
