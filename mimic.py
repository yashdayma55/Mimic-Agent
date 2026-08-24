"""
MimicAgent front door — pick a saved workflow or give the agent a goal.
"""

import os
import sys
import subprocess
import sqlite3

from menu import choose_and_run
from agent_run import run_goal
from trained_workflows import save_trained, list_trained, load_trained
from auto_runner import run_trained
from library import list_workflows
from transcribe import transcribe, load_edited_transcript
from harness_store import save_harness, load_harness, load_harness_steps, list_harness
from harness import run_harness
from harness_schema import step_from_dict
from distill import distill_recording
from workflow_folder import (
    safe_name,
    resolve_paths,
    create_workflow_folder,
    workflow_exists,
    recording_db,
    list_workflow_folders,
)


def _read_goal():
    """Read a single-line goal; empty cancels back to the menu."""
    print("Describe any small bounded task in plain language.")
    print("  e.g. go to example.com, search for X, click the first result")
    return input("Enter your goal: ").strip()


def _chat_loop():
    """Repeated one-line goals via the same run_goal; quit/exit returns to menu."""
    print("Describe tasks in plain language. Type 'quit' to return to the menu.")
    empty_streak = 0
    while True:
        try:
            line = input("task> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line.lower() in ("quit", "exit"):
            break
        if not line:
            empty_streak += 1
            if empty_streak >= 2:
                break
            print("(empty — type a task, or press Enter again to leave chat)")
            continue
        empty_streak = 0
        result = run_goal(line)
        print(f"\n=== loop ended: {result} ===")
        print("done - what next?")


def _train_workflow():
    """Human-in-the-loop training run -> save as a named trained workflow."""
    goal = _read_goal()
    if not goal:
        print("no goal entered.")
        return
    print("\n[train] approving each step (y / s / correction). "
          "Verified actions will be saved as hints.")
    outcome, trace = run_goal(goal, record_trace=True)
    print(f"\n=== training run ended: {outcome} ===")
    if outcome != "done":
        print("  training only saves on a successful 'done' run. not saving.")
        return
    if not trace:
        print("  no verified steps were recorded. not saving.")
        return
    print(f"  recorded {len(trace)} verified step(s).")
    name = input("  name this trained workflow: ").strip()
    if not name:
        print("  no name — not saving.")
        return
    try:
        stem = save_trained(name, goal, trace)
        print(f"  saved as '{stem}' ({len(trace)} hints).")
    except FileExistsError as e:
        ans = input(f"  {e}\n  overwrite? (y/n): ").strip().lower()
        if ans == "y":
            stem = save_trained(name, goal, trace, overwrite=True)
            print(f"  overwritten '{stem}'.")
        else:
            print("  not saved.")


def _run_trained_workflow():
    """Pick a trained workflow and auto-run it (hint-guided, no per-step y/n)."""
    names = list_trained()
    if not names:
        print("  no trained workflows yet. use option 4 to train one.")
        return
    print("\nTrained workflows:")
    for i, n in enumerate(names, 1):
        wf = load_trained(n)
        n_hints = len((wf or {}).get("trace") or [])
        g = ((wf or {}).get("goal") or "")[:60]
        print(f"  {i}. {n}  ({n_hints} hints)  {g}")
    raw = input("\npick number (or name): ").strip()
    if not raw:
        print("cancelled.")
        return
    chosen = None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(names):
            chosen = names[idx - 1]
    else:
        if raw in names:
            chosen = raw
        elif ("trained_" + raw) in names:
            chosen = "trained_" + raw
        else:
            if load_trained(raw):
                chosen = raw
    if not chosen:
        print("invalid pick.")
        return
    print("\n  Auto mode: no per-step approval.")
    print("  Press Ctrl+Alt+P (or create a PAUSE file) to pause / edit goal / stop.\n")
    result = run_trained(chosen)
    print(f"\n=== auto run ended: {result} ===")


def _pick_from_list(names, prompt="pick number (or name): "):
    """Shared picker: number or name from a list. Returns chosen stem or None."""
    raw = input(f"\n{prompt}").strip()
    if not raw:
        return None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(names):
            return names[idx - 1]
        return None
    if raw in names:
        return raw
    return raw  # let caller try load by name


def _transcribe_recording():
    """Option 6: pick a recorded workflow -> transcript.txt/json -> optional save."""
    names = list_workflows()
    if not names:
        print("  no recorded workflows yet.")
        return
    print("\nRecorded workflows (to transcribe):")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n}")
    chosen = _pick_from_list(names)
    if not chosen:
        print("cancelled.")
        return
    if chosen not in names:
        # allow path or exact library name
        pass
    out_txt = "transcript.txt"
    out_json = "transcript.json"
    try:
        steps, inputs = transcribe(chosen, out_txt=out_txt, out_json=out_json)
    except Exception as e:
        print(f"  transcribe failed: {e}")
        return
    print(f"\n  Edit the transcript here:")
    print(f"    {out_txt}   (human-readable)")
    print(f"    {out_json}  (structured HarnessSteps)")
    if inputs:
        print(f"  Declared INPUTS: {', '.join('{'+x+'}' for x in inputs)}")
    ans = input("\n  save as a harness workflow now? (y/n): ").strip().lower()
    if ans != "y":
        print("  (you can save later via option 7 after editing transcript.json)")
        return
    name = input("  name this harness workflow: ").strip()
    if not name:
        print("  no name — not saving.")
        return
    try:
        stem = save_harness(name, steps, inputs=inputs)
        print(f"  saved as '{stem}'. Run it with option 7.")
    except FileExistsError as e:
        ow = input(f"  {e}\n  overwrite? (y/n): ").strip().lower()
        if ow == "y":
            stem = save_harness(name, steps, inputs=inputs, overwrite=True)
            print(f"  overwritten '{stem}'.")
        else:
            print("  not saved.")


def _prompt_inputs(declared):
    """Ask the user to fill each declared {placeholder}. Returns dict."""
    values = {}
    if not declared:
        return values
    print("\nFill inputs for this run (Enter to leave empty):")
    for key in declared:
        try:
            val = input(f"  {{{key}}} = ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        values[key] = val
    return values


def _workflow_folder_info(name):
    """Summary line for a workflows/<name>/ folder."""
    paths = resolve_paths(name)
    n_steps = 0
    inps = []
    if os.path.isfile(paths["transcript_json"]):
        try:
            import json
            with open(paths["transcript_json"], "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                n_steps = len(data.get("steps") or [])
                inps = list(data.get("inputs") or [])
        except Exception:
            pass
    has_transcript = os.path.isfile(paths["transcript_json"])
    return n_steps, inps, has_transcript


def print_workflow_folder_menu():
    """List workflows/ subfolders (for menu display / self-test)."""
    names = list_workflow_folders()
    if not names:
        print("  (no workflow folders yet — use option 8 to record one)")
        return names
    print("\nWorkflow folders (workflows/<name>/):")
    for i, n in enumerate(names, 1):
        n_steps, inps, ok = _workflow_folder_info(n)
        extra = f"  ({n_steps} steps)" if ok else "  (no transcript yet)"
        if inps:
            extra += f"  inputs={inps}"
        print(f"  {i}. {n}{extra}")
    return names


def _pick_workflow_folder(prompt="pick workflow folder (number or name): "):
    """Pick a named folder under workflows/. Returns stem or None."""
    names = print_workflow_folder_menu()
    if not names:
        return None
    raw = input(f"\n{prompt}").strip()
    if not raw:
        print("cancelled.")
        return None
    if raw.isdigit():
        idx = int(raw)
        if 1 <= idx <= len(names):
            return names[idx - 1]
        print("invalid number.")
        return None
    stem = safe_name(raw)
    if stem in names:
        return stem
    if workflow_exists(stem):
        return stem
    print("invalid pick.")
    return None


def _prompt_start_index(n_steps):
    """Return 0-based index; Enter means step 1."""
    raw = input(f"  Start from step # (Enter for 1, max {n_steps}): ").strip()
    if not raw:
        return 0
    if raw.isdigit():
        n = int(raw)
        if 1 <= n <= n_steps:
            return n - 1
    print("  invalid — starting at step 1.")
    return 0


def _run_workflow_folder():
    """Run harness from workflows/<name>/transcript.json."""
    chosen = _pick_workflow_folder()
    if not chosen:
        return
    paths = resolve_paths(chosen)
    if not os.path.isfile(paths["transcript_json"]):
        print(f"  no transcript at {paths['transcript_json']}")
        print("  transcribe this workflow first (option 8 or 6).")
        return
    try:
        steps, declared = load_edited_transcript(
            paths["transcript_txt"], paths["transcript_json"]
        )
    except Exception as e:
        print(f"  could not load transcript: {e}")
        return
    if not steps:
        print("  workflow has no steps.")
        return
    inputs = _prompt_inputs(declared)
    start_index = _prompt_start_index(len(steps))
    print(f"\n  Running '{chosen}' ({len(steps)} steps) with approval.\n")
    transcript = run_harness(
        steps, inputs=inputs, require_approval=True, start_index=start_index
    )
    oks = sum(1 for t in transcript if t.get("ok") or t.get("outcome") == "done")
    print(f"\n=== harness run ended: {len(transcript)} records, "
          f"{oks} ok/done ===")


def _run_harness_workflow():
    """Option 7: run a harness workflow or a workflows/<name>/ folder."""
    print("\nRun workflow from:")
    print("  1. Harness library (harness_*.json)")
    print("  2. Workflow folder (workflows/<name>/transcript.json)")
    sub = input("choice [1]: ").strip() or "1"
    if sub == "2":
        _run_workflow_folder()
        return

    names = list_harness()
    if not names:
        print("  no harness workflows yet. use option 6 to transcribe a recording.")
        return
    print("\nHarness workflows:")
    for i, n in enumerate(names, 1):
        wf = load_harness(n)
        n_steps = len((wf or {}).get("steps") or [])
        inps = (wf or {}).get("inputs") or []
        extra = f"  inputs={inps}" if inps else ""
        print(f"  {i}. {n}  ({n_steps} steps){extra}")
    chosen = _pick_from_list(names)
    if not chosen:
        print("cancelled.")
        return
    if chosen not in names:
        if ("harness_" + chosen) in names:
            chosen = "harness_" + chosen
        elif not load_harness(chosen):
            print("invalid pick.")
            return
    wf = load_harness(chosen)
    if not wf:
        print(f"  could not load '{chosen}'")
        return
    steps = [step_from_dict(s) for s in wf.get("steps") or [] if isinstance(s, dict)]
    if not steps:
        # try load_harness_steps
        steps = load_harness_steps(chosen) or []
    if not steps:
        print("  workflow has no steps.")
        return
    declared = list(wf.get("inputs") or [])
    inputs = _prompt_inputs(declared)
    start_index = _prompt_start_index(len(steps))
    print(f"\n  Running harness '{chosen}' ({len(steps)} steps) "
          f"with approval on each action.\n")
    transcript = run_harness(
        steps, inputs=inputs, require_approval=True, start_index=start_index
    )
    oks = sum(1 for t in transcript if t.get("ok") or t.get("outcome") == "done")
    print(f"\n=== harness run ended: {len(transcript)} records, "
          f"{oks} ok/done ===")


def _clear_recording_db(db_path="recording.db"):
    """Start a fresh capture so a new record session does not mix old events."""
    if not os.path.isfile(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM events")
        conn.commit()
        conn.close()
        print(f"  cleared prior events in {db_path}")
    except Exception as e:
        print(f"  (could not clear {db_path}: {e}; continuing)")


def _record_transcribe_edit_run():
    """Option 8: name workflow -> record -> distill -> transcribe -> edit -> run."""
    print("\n=== Record, transcribe, edit, then run ===")
    print("  Each workflow gets its own folder under workflows/<name>/")
    print("  (recording.db, captures/, plan, transcript — fully isolated).\n")

    wf_name = input("  Workflow name: ").strip()
    if not wf_name:
        print("  no name — cancelled.")
        return

    overwrite = False
    if workflow_exists(wf_name):
        ans = input(
            f"  workflows/{safe_name(wf_name)}/ already exists. Overwrite? (y/n): "
        ).strip().lower()
        if ans != "y":
            print("  cancelled (pick another name or confirm overwrite).")
            return
        overwrite = True

    try:
        paths = create_workflow_folder(wf_name, overwrite=overwrite)
    except FileExistsError as e:
        print(f"  {e}")
        return

    print(f"\n  Workflow folder: {paths['workflow_dir']}")
    print("  1) Demonstrate the workflow (Esc stops the recorder).")
    print("  2) A transcript will be written into this folder.")
    print("  3) Press Enter here when done editing; harness runs with approval.\n")
    ready = input("  Ready to record? (y/n): ").strip().lower()
    if ready != "y":
        print("cancelled.")
        return

    _clear_recording_db(paths["recording_db"])
    recorder = os.path.join(os.path.dirname(os.path.abspath(__file__)) or ".",
                            "mini_recorder.py")
    print(f"\n  Starting recorder (output -> {paths['workflow_dir']})...\n")
    try:
        rc = subprocess.call(
            [sys.executable, recorder, paths["workflow_dir"]],
            cwd=os.path.dirname(recorder) or ".",
        )
    except Exception as e:
        print(f"  recorder failed to start: {e}")
        return
    if rc not in (0, None):
        print(f"  recorder exited with code {rc}")

    if not os.path.isfile(paths["recording_db"]):
        print(f"  no recording.db at {paths['recording_db']}. aborting.")
        return

    print("\n  Distilling recording -> plan.json ...")
    try:
        distill_recording(
            paths["recording_db"],
            paths["plan_txt"],
            paths["plan_json"],
        )
    except Exception as e:
        print(f"  distill failed: {e}")
        return

    out_txt = paths["transcript_txt"]
    out_json = paths["transcript_json"]
    print("\n  Transcribing plan.json -> transcript ...")
    try:
        steps, declared = transcribe(
            paths["plan_json"], out_txt=out_txt, out_json=out_json
        )
    except Exception as e:
        print(f"  transcribe failed: {e}")
        return
    if not steps:
        print("  transcript has no steps. aborting.")
        return

    print(f"\n  Edit the transcript, then come back here:")
    print(f"    {os.path.abspath(out_txt)}")
    if declared:
        print(f"  Declared INPUTS: {', '.join('{'+x+'}' for x in declared)}")
    try:
        input("\n  Press Enter when you are done editing (or Ctrl+C to cancel)... ")
    except (EOFError, KeyboardInterrupt):
        print("\ncancelled.")
        return

    try:
        steps, declared = load_edited_transcript(out_txt, out_json)
    except Exception as e:
        print(f"  failed to load edited transcript: {e}")
        return
    if not steps:
        print("  no steps after edit. aborting.")
        return

    inputs = _prompt_inputs(declared)
    start_index = _prompt_start_index(len(steps))
    print(f"\n  Running harness ({len(steps)} steps) with approval on each action.\n")
    transcript = run_harness(
        steps, inputs=inputs, require_approval=True, start_index=start_index
    )
    oks = sum(1 for t in transcript if t.get("ok") or t.get("outcome") == "done")
    print(f"\n=== harness run ended: {len(transcript)} records, "
          f"{oks} ok/done ===")

    ans = input("\n  save this harness workflow for later? (y/n): ").strip().lower()
    if ans == "y":
        name = input("  name: ").strip()
        if name:
            try:
                stem = save_harness(name, steps, inputs=declared)
                print(f"  saved as '{stem}'. Run later with option 7.")
            except FileExistsError as e:
                ow = input(f"  {e}\n  overwrite? (y/n): ").strip().lower()
                if ow == "y":
                    stem = save_harness(name, steps, inputs=declared, overwrite=True)
                    print(f"  overwritten '{stem}'.")
                else:
                    print("  not saved.")
        else:
            print("  no name — not saving.")


def _open_visual_editor():
    """Option 9: pick workflow folder, start review server, print URL."""
    chosen = _pick_workflow_folder(
        prompt="open visual editor for (number or name): "
    )
    if not chosen:
        return
    paths = resolve_paths(chosen)
    if not os.path.isfile(paths["transcript_json"]):
        print(f"  warning: no transcript yet at {paths['transcript_json']}")
        print("  editor will open; transcribe or record first to see steps.")

    root = os.path.dirname(os.path.abspath(__file__)) or "."
    server_py = os.path.join(root, "review_server.py")
    if not os.path.isfile(server_py):
        print(f"  missing {server_py}")
        return
    url = f"http://127.0.0.1:8765/?workflow={chosen}"
    print("\n=== Visual workflow editor ===")
    print(f"  Workflow: {chosen}")
    print(f"  Folder:   {paths['workflow_dir']}")
    print(f"  Open in browser:\n    {url}")
    print("  Edit/Save per step or Save all; Run sends approvals here.")
    print("  Press Enter to stop the server and return to the menu.\n")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", server_py, chosen],
            cwd=root,
        )
    except Exception as e:
        print(f"  could not start review_server: {e}")
        return
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        print("  editor stopped.")


def main():
    while True:
        print("\n=== MimicAgent ===")
        print("  1. Run a saved workflow")
        print("  2. Give the agent a goal")
        print("  3. Chat with the agent")
        print("  4. Train a workflow")
        print("  5. Run a trained workflow (auto)")
        print("  6. Transcribe a recording into an editable workflow")
        print("  7. Run a harness workflow")
        print("  8. Record, transcribe, edit, then run")
        print("  9. Open visual workflow editor")
        print("  0. Quit")
        choice = input("\nchoice: ").strip()

        if choice == "1":
            choose_and_run()
        elif choice == "2":
            goal = _read_goal()
            if goal:
                result = run_goal(goal)
                print(f"\n=== loop ended: {result} ===")
            else:
                print("no goal entered.")
        elif choice == "3":
            _chat_loop()
        elif choice == "4":
            _train_workflow()
        elif choice == "5":
            _run_trained_workflow()
        elif choice == "6":
            _transcribe_recording()
        elif choice == "7":
            _run_harness_workflow()
        elif choice == "8":
            _record_transcribe_edit_run()
        elif choice == "9":
            _open_visual_editor()
        elif choice == "0":
            print("bye.")
            break
        else:
            print("invalid choice.")


if __name__ == "__main__":
    main()
