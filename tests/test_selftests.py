import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)

import build_web_bundles  # noqa: E402
import hemis_cli  # noqa: E402
import hemis_mcp_server  # noqa: E402
import hemis_oneid_auth  # noqa: E402
import hemis_split_proxy  # noqa: E402


class SelfTests(unittest.TestCase):
    def test_auth_self_test(self):
        res = hemis_oneid_auth.run_self_test()
        self.assertTrue(res["self_test_passed"])
        self.assertEqual(res["checks_passed"], 23)

    def test_proxy_self_test(self):
        res = hemis_split_proxy.run_self_test()
        self.assertTrue(res["self_test_passed"])
        self.assertEqual(res["checks_passed"], 6)

    def test_web_bundles_self_test(self):
        res = build_web_bundles.run_self_test()
        self.assertTrue(res["self_test_passed"])
        self.assertEqual(res["checks_passed"], 3)

    def test_mcp_server_self_test(self):
        res = hemis_mcp_server.run_self_test()
        self.assertTrue(res["self_test_passed"])
        self.assertEqual(res["checks_passed"], 5)

    def test_version_and_update_checker(self):
        self.assertEqual(hemis_cli.get_local_version(), "1.0.1")
        up = hemis_cli.check_for_updates(fetch_fn=lambda _u, _t: "1.1.0\n", local_version="1.0.1")
        self.assertTrue(up["checked"])
        self.assertTrue(up["update_available"])
        self.assertEqual(up["remote_version"], "1.1.0")

        same = hemis_cli.check_for_updates(fetch_fn=lambda _u, _t: "1.0.1\n", local_version="1.0.1")
        self.assertTrue(same["checked"])
        self.assertFalse(same["update_available"])

        off = hemis_cli.check_for_updates(
            fetch_fn=lambda _u, _t: (_ for _ in ()).throw(OSError("offline")),
            local_version="1.0.1",
        )
        self.assertFalse(off["checked"])
        self.assertFalse(off["update_available"])
        self.assertEqual(off["status"], "offline_or_unreachable")


class CliContract(unittest.TestCase):
    def _run(self, *args, env=None, input_text=None):
        full_env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("HEMIS_ONEID", "HEMIS_STUDENT", "HEMIS_CREDENTIALS"))
        }
        full_env["HEMIS_CREDENTIALS_FILE"] = os.path.join(ROOT, "tests", "_nonexistent_vault.json")
        full_env.update(env or {})
        return subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, "hemis_cli.py"), *args],
            input=input_text,
            capture_output=True, text=True, env=full_env, timeout=120,
        )

    def test_doctor_json(self):
        r = self._run("doctor", "--offline")
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["version"], "1.0.1")
        self.assertEqual(payload["auth_self_test"], 23)
        self.assertEqual(payload["proxy_self_test"], 6)
        self.assertEqual(payload["bundle_self_test"], 3)
        self.assertEqual(payload["mcp_self_test"], 5)

    def test_bundle_cli_outputs_valid_artifacts(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            r = self._run("bundle", "--output-dir", tmpdir)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertTrue(out["ok"])
            self.assertTrue(os.path.isfile(out["web_skill_zip"]["zip_path"]))
            self.assertTrue(os.path.isfile(out["chatgpt_project"]["bundle_path"]))
            self.assertTrue(os.path.isfile(out["chatgpt_project"]["instructions_path"]))
            self.assertTrue(os.path.isfile(out["openapi"]["path"]))

    def test_auth_save_status_clear_cli(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            vfile = os.path.join(tmpdir, "vault.json")
            env = {"HEMIS_CREDENTIALS_FILE": vfile}
            r_save = self._run(
                "auth-save", "--stdin",
                env=env,
                input_text=json.dumps({"login": "teacher01", "password": "MyPassword123"}),
            )
            self.assertEqual(r_save.returncode, 0, r_save.stderr)
            self.assertNotIn("MyPassword123", r_save.stdout)
            save_out = json.loads(r_save.stdout)
            self.assertTrue(save_out["ok"])
            self.assertEqual(save_out["login_masked"], "te***01")

            r_stat = self._run("auth-status", env=env)
            self.assertEqual(r_stat.returncode, 0)
            stat_out = json.loads(r_stat.stdout)
            self.assertTrue(stat_out["configured"])
            self.assertEqual(stat_out["source"], "vault")
            self.assertNotIn("MyPassword123", r_stat.stdout)

            r_clr = self._run("auth-clear", env=env)
            self.assertEqual(r_clr.returncode, 0)
            self.assertFalse(os.path.exists(vfile))

            r_bom = self._run(
                "auth-save", "--stdin",
                env=env,
                input_text="\ufeff" + json.dumps({"login": "teacher02", "password": "MyPassword456"}),
            )
            self.assertEqual(r_bom.returncode, 0, r_bom.stderr)
            self.assertNotIn("MyPassword456", r_bom.stdout)
            bom_out = json.loads(r_bom.stdout)
            self.assertTrue(bom_out["ok"])
            self.assertEqual(bom_out["login_masked"], "te***02")
            r_clr2 = self._run("auth-clear", env=env)
            self.assertEqual(r_clr2.returncode, 0)

    def test_login_requires_env_and_never_echoes(self):
        r = self._run("login")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_student_get_requires_env(self):
        r = self._run("student-get", "account/me")
        self.assertEqual(r.returncode, 2)
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_no_secrets_in_repo(self):
        needles = ("Bearer eyJ", "BEGIN PRIVATE KEY")
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "dist")]
            for name in files:
                if name == "test_selftests.py":
                    continue
                with open(os.path.join(dirpath, name), encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                for needle in needles:
                    self.assertNotIn(needle, text, f"{needle!r} found in {name}")


if __name__ == "__main__":
    unittest.main()
