"""The import page's render code, run for real (node) against stand-in elements: a ReferenceError in it once left a
paused import with no Resume button and an empty log (renderImportPulse was handed a variable that lived in the
caller). Skips without node."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HOME_JS = Path(__file__).resolve().parents[1] / "src/decompnet/app/static/js/home.js"
HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const grab = (name) => { const i = src.indexOf(name); let d = 0, j = src.indexOf("{", i); for (let k = j; ; k++) { if (src[k] === "{") d++; if (src[k] === "}" && --d === 0) return src.slice(i, k + 1); } };
const els = {};
const el = () => new Proxy({ style: {}, children: [], firstElementChild: { style: {} }, scrollTop: 0, clientHeight: 0, scrollHeight: 0, textContent: "" }, {});
globalThis.$ = (id) => (els[id] ??= el());
globalThis.JOB_POLL_MS = 500;
const code = `const IMPORT_QUIET_S = 600; ${grab("function spanShown")} ${grab("function renderImportPulse")} ${grab("function renderImportPage")}; globalThis.render = renderImportPage;`;
eval(code);

const base = { id: "abc123", title: "Cm Decomp", kind: "folder", source: "C:/x", state: "interrupted", error: "closed",
  log: ["[3 of 18] x"], job: null, parent: "P", done: {}, failures: [], now: 2000, job_last: 1000, job_started: 500,
  analysis: { count: 15, newest: 1500, folder: "F" } };
const out = {};
for (const state of ["interrupted", "failed", "running", "cancelling", "done"]) {
  render({ ...base, state }, base.log);
  out[state] = { resumeHidden: els.impResume.hidden, cancel: els.impCancel.textContent, log: els.impLog.textContent,
                 quiet: !els.impQuiet.hidden };
}
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_a_paused_import_page_shows_resume_and_its_log(tmp_path):
    script = tmp_path / "harness.js"
    script.write_text(HARNESS, encoding="utf-8")
    ran = subprocess.run(["node", str(script), str(HOME_JS)], capture_output=True, text=True, timeout=60)
    assert ran.returncode == 0, ran.stderr
    got = json.loads(ran.stdout)
    for state in ("interrupted", "failed"):
        assert got[state]["resumeHidden"] is False and got[state]["cancel"] == "Remove import"
        assert got[state]["log"] == "[3 of 18] x"
    for state in ("running", "cancelling", "done"):
        assert got[state]["resumeHidden"] is True
    assert got["running"]["cancel"] == "Cancel import" and got["running"]["quiet"] is True  # 17 min without output
    assert got["interrupted"]["quiet"] is False
