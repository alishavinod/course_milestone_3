import os
import platform
import signal
import subprocess
from typing import Any

from pydantic import BaseModel

from minisweagent.exceptions import Submitted
from minisweagent.tools.app_coverage import Coverage
from minisweagent.tools.independent_reviewer import IndependentReviewer
from minisweagent.tools.section_driver import SectionDriver, passive_opens
from minisweagent.utils.serialize import recursive_merge


class LocalEnvironmentConfig(BaseModel):
    cwd: str = ""
    env: dict[str, str] = {}
    timeout: int = 30
    app_coverage: bool = False
    """Report which routes and UI hooks the app serves, every few commands and through `app-coverage`."""
    app_coverage_every: int = 8
    app_coverage_log_path: str = "/logs/agent/app_coverage.jsonl"
    restart_app: bool = False
    """Tell the agent about the `restart-app` command (Jessica's improvement; the command itself is always installed)."""
    section_driver: bool = False
    """Walk the agent through the task's sections one at a time, and refuse a submit until all are done."""
    section_driver_open_after_seconds: int = 2520
    section_driver_open_after_cost: float = 2.6
    section_driver_log_path: str = "/logs/agent/section_driver.jsonl"
    independent_reviewer: bool = False
    """Have the same model review the app in new conversations when the agent submits."""
    independent_reviewer_open_after_seconds: int = 2520
    independent_reviewer_open_after_cost: float = 2.6
    independent_reviewer_log_path: str = "/logs/agent/independent_reviewer.jsonl"


class LocalEnvironment:
    def __init__(self, *, config_class: type = LocalEnvironmentConfig, **kwargs):
        """This class executes bash commands directly on the local machine."""
        self.config = config_class(**kwargs)
        self._coverage = (
            Coverage(self.config.app_coverage_every, self.config.app_coverage_log_path)
            if self.config.app_coverage
            else None
        )
        self._driver = (
            SectionDriver(
                self.config.section_driver_open_after_seconds,
                self.config.section_driver_open_after_cost,
                self.config.section_driver_log_path,
            )
            if self.config.section_driver
            else None
        )
        self._reviewer = (
            IndependentReviewer(
                self.config.independent_reviewer_open_after_seconds,
                self.config.independent_reviewer_open_after_cost,
                self.config.independent_reviewer_log_path,
            )
            if self.config.independent_reviewer
            else None
        )

    def on_task(self, task: str, cost, ask) -> None:
        """Called by the agent before the first message: the task text, the money spent, and a way to ask the model separately."""
        if self._driver:
            self._driver.begin(task, cost)
        if self._reviewer:
            self._reviewer.begin(task, cost, ask)

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        """Execute a command in the local environment and return the result as a dict."""
        command = action.get("command", "")
        cwd = cwd or self.config.cwd or os.getcwd()
        opened = passive_opens() if self._driver else 0
        try:
            result = _run(command, cwd, os.environ | self.config.env, timeout or self.config.timeout)
            output = {"output": result.stdout, "returncode": result.returncode, "exception_info": ""}
        except Exception as e:
            raw_output = getattr(e, "output", None)
            raw_output = (
                raw_output.decode("utf-8", errors="replace") if isinstance(raw_output, bytes) else (raw_output or "")
            )
            output = {
                "output": raw_output,
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        if self._driver:
            self._driver.observe(passive_opens() - opened)
            self._driver.rewrite(output)
        self._check_finished(output)
        if self._coverage:
            output["output"] += self._coverage.after_command()
        return output

    def _check_finished(self, output: dict):
        """Raises Submitted if the output indicates task completion."""
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" and output["returncode"] == 0:
            refusal = self._driver.check_submit() if self._driver else None
            refusal = refusal or (self._reviewer.check_submit() if self._reviewer else None)
            if refusal:
                output["output"], output["returncode"] = refusal, 1
                return
            submission = "".join(lines[1:])
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {"exit_status": "Submitted", "submission": submission},
                }
            )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        tool_vars = {"section_driver_intro": self._driver.intro()} if self._driver else {}
        return recursive_merge(self.config.model_dump(), platform.uname()._asdict(), os.environ, tool_vars, kwargs)

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }


def _run(command: str, cwd: str, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Like subprocess.run, but kills the whole process group on timeout so no children are orphaned."""
    process = subprocess.Popen(
        command,
        shell=True,
        text=True,
        cwd=cwd,
        env=env,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL) if os.name == "posix" else process.kill()
        stdout, _ = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout)
    return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)
