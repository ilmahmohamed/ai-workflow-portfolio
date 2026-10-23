"""Checks for the GitHub Actions workflow (.github/workflows/pipeline.yml). Run:  python tests\\test_workflow.py

Plain-text checks only (no YAML library is installed or pinned). Nothing is run and nothing is sent.
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "pipeline.yml"

SECRETS = {
    "GEMINI_API_KEY", "MS_CLIENT_ID", "MS_TOKEN_CACHE", "SLACK_WEBHOOK_URL",
    "SLACK_LOG_WEBHOOK_URL", "ATTIO_API_KEY", "ATTIO_DEAL_OWNER",
}
EVERY_15_MINUTES = "*/15 * * * *"
DAILY = "7 8 * * *"     # off the 15-minute grid, so the daily run never lands on a triage run


def text():
    return WORKFLOW.read_text(encoding="utf-8")


def jobs():
    """{job name: its text}. A job starts at a line with two spaces and a name, inside jobs:."""
    body = text().split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([A-Za-z0-9_-]+):\s*$", body, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1] for i in range(1, len(parts), 2)}


def run_lines():
    return [line.strip() for line in text().splitlines() if "outlook_triage.py" in line or "leads_to_attio.py" in line]


class WorkflowTests(unittest.TestCase):
    def test_the_file_exists(self):
        self.assertTrue(WORKFLOW.is_file())

    # ---- When it runs ------------------------------------------------------

    def test_runs_every_15_minutes_and_once_a_day(self):
        self.assertIn(f"- cron: '{EVERY_15_MINUTES}'", text())
        self.assertIn(f"- cron: '{DAILY}'", text())
        self.assertNotIn(int(DAILY.split()[0]), (0, 15, 30, 45))

    def test_has_a_manual_button_with_a_dry_run_or_live_choice(self):
        t = text()
        self.assertIn("workflow_dispatch:", t)
        self.assertRegex(t, r"type: choice")
        self.assertRegex(t, r"options:\s*\n\s*- dry-run\s*\n\s*- live")
        self.assertRegex(t, r"default: dry-run")

    def test_only_the_expected_triggers_exist(self):
        t = text()
        for bad in ("pull_request", "pull_request_target", "push:", "issue_comment", "workflow_run"):
            self.assertNotIn(bad, t)

    # ---- The switch --------------------------------------------------------

    def test_every_job_only_runs_when_the_pipeline_is_switched_on(self):
        self.assertGreaterEqual(len(jobs()), 2)
        for name, body in jobs().items():
            with self.subTest(job=name):
                self.assertRegex(body, r"if:.*vars\.PIPELINE_ENABLED == 'true'")

    def test_the_switch_is_never_turned_on_by_the_file(self):
        t = text()
        self.assertNotRegex(t, r"PIPELINE_ENABLED\s*[:=]\s*['\"]?true")
        self.assertNotIn("variable set", t)
        self.assertNotIn("gh variable", t)

    # ---- One at a time, and safe limits ------------------------------------

    def test_the_pipeline_never_runs_twice_at_once(self):
        body = jobs()["pipeline"]
        self.assertRegex(body, r"concurrency:\s*\n\s*group: \S+\s*\n\s*cancel-in-progress: false")

    def test_every_job_has_a_time_limit(self):
        for name, body in jobs().items():
            with self.subTest(job=name):
                self.assertRegex(body, r"timeout-minutes: \d+")

    def test_the_pipeline_job_outlasts_the_worst_case_of_gemini_failing(self):
        # Three emails in a row, each: a 120 s request, a 10 s wait, a 120 s retry = 12.5 minutes, plus set-up and Attio.
        limit = int(re.search(r"timeout-minutes: (\d+)", jobs()["pipeline"]).group(1))
        self.assertGreaterEqual(limit, 20)
        self.assertLessEqual(limit, 30, "a stuck run must not block the queue for long")

    def test_the_workflow_can_only_read_the_repo(self):
        self.assertRegex(text(), re.compile(r"^permissions:\s*\n  contents: read\s*$", re.MULTILINE))

    def test_actions_are_pinned_to_exact_commits(self):
        uses = re.findall(r"uses:\s*(\S+)", text())
        self.assertTrue(uses)
        for item in uses:
            self.assertRegex(item, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", item)

    # ---- What it runs ------------------------------------------------------

    def test_triage_then_attio_with_the_right_switches(self):
        triage = [l for l in run_lines() if "outlook_triage.py" in l]
        attio = [l for l in run_lines() if "leads_to_attio.py" in l]
        self.assertEqual((len(triage), len(attio)), (1, 1))
        for needed in ("--slack live", "--drafts on", "--stateless"):
            self.assertIn(needed, triage[0])
        self.assertIn("--stateless", attio[0])
        self.assertLess(text().index("outlook_triage.py"), text().index("leads_to_attio.py"))

    def test_live_comes_only_from_a_schedule_or_the_live_choice(self):
        for line in run_lines():
            self.assertNotIn("--live", line)           # only through the variable
            self.assertIn("$LIVE", line)
        t = text()
        self.assertIn("inputs.mode", t)
        self.assertIn("--live", t)

    def test_both_steps_go_through_the_log_filter_and_keep_their_exit_code(self):
        for line in run_lines():
            self.assertIn("2>&1 | python workflows/02-email-to-attio/log_filter.py", line)
        self.assertIn("shell: bash", text())            # bash on Actions turns on pipefail

    def test_libraries_come_only_from_the_pinned_file(self):
        installs = re.findall(r"pip install.*", text())
        self.assertEqual(installs, ["pip install -r requirements.txt"])

    def test_python_version_is_fixed(self):
        self.assertRegex(text(), r"python-version: '3\.12'")

    def test_no_state_is_kept_anywhere(self):
        t = text()
        for bad in ("actions/cache", "upload-artifact", "git commit", "git push", "git add"):
            self.assertNotIn(bad, t)

    # ---- Secrets and logs --------------------------------------------------

    def test_secrets_are_used_by_name_only_and_only_as_environment_values(self):
        refs = re.findall(r"secrets\.([A-Z_]+)", text())
        self.assertEqual(set(refs), SECRETS)
        for line in text().splitlines():
            if "secrets." in line:
                self.assertRegex(line.strip(), r"^([A-Z_]+): \$\{\{ secrets\.\1 \}\}$", line)

    def test_nothing_prints_the_environment_or_turns_on_tracing(self):
        t = text()
        for bad in ("set -x", "set -o xtrace", "printenv", "env |", "ACTIONS_STEP_DEBUG", "ACTIONS_RUNNER_DEBUG", "::debug"):
            self.assertNotIn(bad, t)
        for line in t.splitlines():
            if re.match(r"\s*(echo|printf)\b", line):
                for name in SECRETS:
                    self.assertNotIn(name, line)

    def test_no_value_or_web_address_for_a_service_is_written_in_the_file(self):
        t = text()
        for bad in ("hooks.slack.com", "AIza", "xoxb-", "ghp_", "eyJ", "@gmail", "@outlook", "@hotmail"):
            self.assertNotIn(bad, t)

    # ---- Alerts ------------------------------------------------------------

    def test_a_failed_step_posts_its_name_and_a_run_link_to_automation_log(self):
        body = jobs()["pipeline"]
        alert = body[body.index("if: failure()"):]
        self.assertIn("SLACK_LOG_WEBHOOK_URL", alert)
        for needed in ("github.server_url", "github.repository", "github.run_id", "steps.install.outcome",
                       "steps.triage.outcome", "steps.attio.outcome"):
            self.assertIn(needed, alert)
        self.assertNotIn("SLACK_WEBHOOK_URL: ", alert.replace("SLACK_LOG_WEBHOOK_URL", ""))
        for private in ("sender", "subject", "body", "from", "email", "address"):
            # (the folder name 02-email-to-attio in the script's path is not a detail of any email)
            self.assertNotRegex(alert.lower().replace("github.", "").replace("workflows/02-email-to-attio/", ""),
                                rf"\b{private}\b")

    def alert_step(self):
        body = jobs()["pipeline"]
        return body[body.index("if: failure()"):]

    def test_each_step_keeps_a_copy_of_its_filtered_log_for_the_alert(self):
        # The copy is made AFTER the filter, so it never holds an address. It stays on the runner's temporary disk.
        for needle, name in (("outlook_triage.py", "triage.log"), ("leads_to_attio.py", "attio.log")):
            line = [l for l in run_lines() if needle in l][0]
            self.assertRegex(line, r"log_filter\.py \| tee \"\$RUNNER_TEMP/" + re.escape(name) + r"\"$")

    def test_the_alert_builds_its_message_with_the_helper_and_still_posts_if_that_fails(self):
        alert = self.alert_step()
        self.assertIn("alert_message.py", alert)
        self.assertRegex(alert, r"timeout \d+ python workflows/02-email-to-attio/alert_message\.py \"\$STEP\" 2>/dev/null")
        # the plain message is the fallback when the helper gives nothing
        self.assertRegex(alert, r"Pipeline step failed: \$STEP\. Run: \$RUN_URL")
        self.assertIn("curl", alert)
        self.assertLess(alert.index("alert_message.py"), alert.index("curl"))
        self.assertRegex(alert, r"\|\| PAYLOAD=")
        self.assertRegex(alert, r"-z \"\$PAYLOAD\"")

    def test_the_alert_step_can_read_the_mailbox_but_only_through_the_named_secrets(self):
        alert = self.alert_step()
        for name in ("MS_CLIENT_ID", "MS_TOKEN_CACHE", "SLACK_LOG_WEBHOOK_URL"):
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", alert)
        for name in SECRETS - {"MS_CLIENT_ID", "MS_TOKEN_CACHE", "SLACK_LOG_WEBHOOK_URL"}:
            self.assertNotIn(name, alert, "the alert step is given only the secrets it needs")
        self.assertIn("RUN_URL:", alert)

    def test_the_alert_posts_what_the_helper_printed_and_only_that(self):
        alert = self.alert_step()
        self.assertRegex(alert, r"--data \"\$PAYLOAD\"")
        self.assertNotIn("echo", alert.replace('|| echo "The alert could not be posted."', ""))
        self.assertNotIn("$(cat", alert)
        self.assertNotIn("triage.log", alert, "the log is read by the helper, never shown or sent by the workflow")
        self.assertNotIn("attio.log", alert)

    def test_the_alert_is_the_last_step_of_the_pipeline_job(self):
        body = jobs()["pipeline"]
        self.assertEqual(body.count("if: failure()"), 1)
        self.assertGreater(body.index("if: failure()"), body.index("id: attio"))
        self.assertNotIn("- name:", body[body.index("if: failure()"):])    # nothing comes after it

    # ---- Pipeline alive ----------------------------------------------------

    def test_one_alive_message_a_day_goes_to_automation_log(self):
        body = jobs()["alive"]
        self.assertIn(f"github.event.schedule == '{DAILY}'", body)
        self.assertIn("SLACK_LOG_WEBHOOK_URL", body)
        self.assertNotIn("outlook_triage.py", body)
        self.assertNotIn("concurrency", body)           # it must never replace a waiting triage run

    def test_the_pipeline_job_skips_the_daily_message_trigger(self):
        self.assertIn(f"github.event.schedule != '{DAILY}'", jobs()["pipeline"])


def job_condition(name):
    return re.search(r"^    if: (.*)$", jobs()[name], flags=re.MULTILINE).group(1)


def live_expression():
    return re.search(r"^      LIVE: \$\{\{ (.*) \}\}$", jobs()["pipeline"], flags=re.MULTILINE).group(1)


def evaluate(expression, enabled, event, mode, schedule):
    """Evaluate a GitHub expression made of ==, !=, &&, || and quoted text, for one pretend trigger."""
    python = (expression
              .replace("vars.PIPELINE_ENABLED", repr(enabled))
              .replace("github.event_name", repr(event))
              .replace("github.event.schedule", repr(schedule))
              .replace("inputs.mode", repr(mode))
              .replace("&&", " and ").replace("||", " or "))
    return eval(python, {"__builtins__": {}}, {})


# (event name, chosen mode, which schedule fired). A scheduled run has no mode.
TRIGGERS = {
    "every-15-minutes": ("schedule", "", EVERY_15_MINUTES),
    "daily": ("schedule", "", DAILY),
    "button-dry-run": ("workflow_dispatch", "dry-run", ""),
    "button-live": ("workflow_dispatch", "live", ""),
}


class SwitchTests(unittest.TestCase):
    """What the PIPELINE_ENABLED switch does, checked against the workflow's own conditions."""

    def test_pipeline_job_truth_table(self):
        expected_when_off = {"every-15-minutes": False, "daily": False, "button-dry-run": True, "button-live": False}
        expected_when_on = {"every-15-minutes": True, "daily": False, "button-dry-run": True, "button-live": True}
        for enabled, expected in (("true", expected_when_on), ("false", expected_when_off), ("", expected_when_off),
                                  ("True", expected_when_off), ("yes", expected_when_off)):
            for name, trigger in TRIGGERS.items():
                with self.subTest(enabled=enabled, trigger=name):
                    self.assertEqual(bool(evaluate(job_condition("pipeline"), enabled, *trigger)), expected[name])

    def test_alive_job_runs_only_for_the_daily_trigger_and_only_when_switched_on(self):
        for enabled in ("true", "false", ""):
            for name, trigger in TRIGGERS.items():
                with self.subTest(enabled=enabled, trigger=name):
                    expected = enabled == "true" and name == "daily"
                    self.assertEqual(bool(evaluate(job_condition("alive"), enabled, *trigger)), expected)

    def test_a_run_that_gets_past_a_switched_off_pipeline_is_never_live(self):
        for enabled in ("false", "", "no"):
            for name, trigger in TRIGGERS.items():
                if evaluate(job_condition("pipeline"), enabled, *trigger):
                    with self.subTest(enabled=enabled, trigger=name):
                        self.assertEqual(evaluate(live_expression(), enabled, *trigger), "")

    def test_live_only_for_scheduled_runs_and_the_live_choice(self):
        expected = {"every-15-minutes": "--live", "daily": "--live", "button-dry-run": "", "button-live": "--live"}
        for name, trigger in TRIGGERS.items():
            with self.subTest(trigger=name):
                self.assertEqual(evaluate(live_expression(), "true", *trigger), expected[name])

    def test_a_dry_run_posts_no_alert(self):
        body = jobs()["pipeline"]
        self.assertIn("if: failure() && env.LIVE != ''", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
