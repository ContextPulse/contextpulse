"""JournalBridge — routes insights to the journal via log-entry.py subprocess."""

import subprocess
import sys
from pathlib import Path


class JournalBridge:
    def __init__(self, scripts_dir: Path | None = None):
        self.scripts_dir = scripts_dir or Path.home() / ".claude/shared-knowledge/scripts"
        self.log_script = self.scripts_dir / "log-entry.py"

    def log_insight(
        self,
        project: str,
        content: str,
        entry_type: str = "observation",
        agent: str = "contextpulse-project",
        session_id: str = "",
    ) -> tuple[bool, str]:
        if not self.log_script.is_file():
            return False, f"Script not found: {self.log_script}"

        cmd = [
            sys.executable,
            str(self.log_script),
            "--type", entry_type,
            "--content", content,
            "--project", project,
            "--agent", agent,
        ]
        if session_id:
            cmd.extend(["--session-id", session_id])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return True, result.stdout.strip()
            return False, result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "Timed out"
        except Exception as e:
            return False, str(e)
