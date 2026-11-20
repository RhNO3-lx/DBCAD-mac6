"""Rebuild the public CAD observation audit. Run from any directory, see README.md."""

import argparse
import ast
import base64
import csv
import hashlib
import io
import json
import random
import re
import shutil
import statistics as stats
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import duckdb
import lxml.html
import pdfplumber
import tiktoken
from PIL import Image

V3 = "114f19d80b6b72763a0d7e2681148417081bbe64"
CADGEN = "d12435f55a6cf5d1ce15443361abb3f595a52908"
V3_REPO = "gnucleus-ai/cad-gen-freecad-bench-v3"
CG_REPO = "HuggingAI4Engineering/cadgenbench-submissions"
WORLD = "fa02aa03b944daf2ae5b23e56d135bb7cbb75994"
REAL_URL = "https://arxiv.org/html/2609.03773v1"
HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fetch(path, url):
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(5):
        try:
            req = Request(url, headers={"User-Agent": "DBCAD-public-observation-study/1"})
            with urlopen(req, timeout=60) as response:
                data = response.read()
            tmp = path.with_suffix(path.suffix + ".partial")
            tmp.write_bytes(data)
            tmp.replace(path)
            return path
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2**attempt)


def hub_file(cache, repo, revision, name, raw=False):
    route = "raw" if raw else "resolve"
    url = f"https://huggingface.co/datasets/{repo}/{route}/{revision}/{quote(name)}"
    return fetch(cache / revision / name, url)


def save(out, name, rows):
    with (out / name).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def pixels(data):
    with Image.open(io.BytesIO(data)) as image:
        rgba = image.convert("RGBA")
        return rgba.width, rgba.height, sha(rgba.tobytes())


def time_value(text):
    return datetime.fromisoformat(text).timestamp()


def union_seconds(spans):
    total, end = 0.0, float("-inf")
    for start, stop in sorted(spans):
        if stop < start:
            raise ValueError("Negative event duration")
        total += max(0, stop - max(end, start))
        end = max(end, stop)
    return total


def paper_tables(cache, out, funcad_pdf):
    page = lxml.html.fromstring(fetch(cache / "realcad-v1.html", REAL_URL).read_bytes())
    rows = []
    for row in page.xpath('//*[@id="A1.T12"]//tr'):
        cells = [" ".join("".join(c.itertext()).split()) for c in row.xpath("./th|./td")]
        if len(cells) != 4 or not cells[0].startswith("rcb_"):
            continue
        for system, text in zip(("Codex + GPT-5.5", "Claude Code + Opus 4.8"), cells[2:]):
            values = re.search(r"([\d.]+) min\s*·\s*([\d.]+)k", text)
            rows.append(
                {
                    "task": cells[0],
                    "system": system,
                    "wall_minutes": float(values[1]) if values else None,
                    "reported_tokens_thousands": float(values[2]) if values else None,
                    "exported": "no exported artifact" not in text,
                }
            )
    assert len(rows) == 50 and len({r["task"] for r in rows}) == 25
    save(out, "assembly_paper_rows.csv", rows)
    if not funcad_pdf.is_file():
        funcad_pdf = fetch(cache / "FunCAD-v1.pdf", "https://arxiv.org/pdf/2610.26861v1")
    bars = defaultdict(list)
    # Figure 9b. Coordinates in PDF points; ignore separators, grid and legend.
    with pdfplumber.open(funcad_pdf) as pdf:
        for r in pdf.pages[9].rects:
            if (
                r["x0"] > 352
                and 100 < r["top"] < 183
                and 12 < r["height"] < 15
                and r["non_stroking_color"] != (1.0, 1.0, 1.0)
            ):
                bars[round(r["top"], 2)].append(r)
    assert len(bars) == 4
    shares = []
    for model, (_, rects) in zip(("Astra", "Kimi", "Fable", "Grok"), sorted(bars.items())):
        rects.sort(key=lambda r: r["x0"])
        assert len(rects) == 5 and 144 < sum(r["width"] for r in rects) < 146
        names = (
            "build_pct",
            "geometry_checks_pct",
            "simulation_or_probes_pct",
            "preview_render_pct",
            "concurrent_pct",
        )
        shares.append(
            dict(
                model=model,
                **{
                    k: round(100 * r["width"] / sum(s["width"] for s in rects), 3)
                    for k, r in zip(names, rects)
                },
            )
        )
    save(out, "funcad_figure9b.csv", shares)
    means = {}
    for system in sorted({r["system"] for r in rows}):
        timed = [r for r in rows if r["system"] == system and r["wall_minutes"] is not None]
        minutes = [r["wall_minutes"] for r in timed]
        means[system] = {
            "timed_trials": len(timed),
            "mean_minutes": stats.mean(minutes),
            "median_minutes": stats.median(minutes),
            "mean_reported_tokens_k": stats.mean(r["reported_tokens_thousands"] for r in timed),
        }
    return {
        "RealCADBench": means,
        "FunCAD_figure9b": shares,
        "FunCAD_pdf_sha256": sha(funcad_pdf.read_bytes()),
    }


def public_v3(cache, out):
    table = hub_file(cache, V3_REPO, V3, "data/cad-bench-v3-runs.parquet")
    assert sha(table.read_bytes()) == "a5f664fb48682ecf0c61753d56b54752d3a5b4f0deec7e1b0d98fe8e0feea5d3"
    db = duckdb.connect()
    q = db.from_parquet(str(table))
    cols = q.columns
    rows = [dict(zip(cols, r)) for r in q.order("system, task_id").fetchall()]
    assert len(rows) == 1300
    group_rows = []
    groups = defaultdict(list)
    for r in rows:
        groups[r["system"], r["workflow"]].append(r)
    for (system, workflow), trials in groups.items():
        group_rows.append(
            {
                "system": system,
                "workflow": workflow,
                "n": len(trials),
                "trials_with_exception": sum(bool(r["exception_type"]) for r in trials),
                "median_trial_s": stats.median(r["duration_sec"] for r in trials),
                "median_input_tokens": stats.median(r["n_input_tokens"] for r in trials),
                "median_cached_tokens": stats.median(r["n_cache_tokens"] for r in trials),
                "median_output_tokens": stats.median(r["n_output_tokens"] for r in trials),
            }
        )
    save(out, "v3_by_system_workflow.csv", group_rows)
    trials = [r for r in rows if r["workflow"] == "create_edit" and r["agent"] in ("codex", "antigravity-cli")]
    assert len(trials) == 120
    jobs = [r["audit_dir_path"] + "/agent/trajectory.json" for r in trials]
    jobs.extend(r["audit_dir_path"] + "/result.json" for r in trials if r["agent"] == "antigravity-cli")
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda name: hub_file(cache, V3_REPO, V3, name), jobs))
    encoder = tiktoken.get_encoding("cl100k_base")
    measurements, images, checks = [], [], []
    for r in trials:
        path = cache / V3 / r["audit_dir_path"]
        traj_bytes = (path / "agent/trajectory.json").read_bytes()
        trace = json.loads(traj_bytes)
        base = {"system": r["system"], "task": r["task_id"]}
        record = dict(
            **base,
            trial_s=r["duration_sec"],
            input_tokens=r["n_input_tokens"],
            cached_tokens=r["n_cache_tokens"],
            output_tokens=r["n_output_tokens"],
            reported_cost_usd=(trace.get("final_metrics") or {}).get("total_cost_usd"),
            reward=r["reward"],
            exception=r["exception_type"],
            agent_s=None,
            tool_results=0,
            geometry_open_calls=None,
            geometry_open_union_s=None,
            geometry_open_proxy_tokens=None,
            all_tool_text_proxy_tokens=None,
            image_blocks=None,
            repeat_file=None,
            repeat_pixels=None,
            trajectory_sha256=sha(traj_bytes),
            raw_directory=r["audit_dir_path"],
        )
        if r["agent"] == "antigravity-cli":
            result = json.loads((path / "result.json").read_bytes())
            record["reported_cost_usd"] = result["agent_result"]["cost_usd"]
            span = result["agent_execution"]
            record["agent_s"] = time_value(span["finished_at"]) - time_value(span["started_at"])
            selected, spans, all_tokens = [], [], 0
            for step in trace["steps"]:
                calls = {c["tool_call_id"]: c for c in step.get("tool_calls", [])}
                for res in step.get("observation", {}).get("results", []):
                    text = res["content"]
                    assert isinstance(text, str) and "data:image/" not in text
                    n_tokens = len(encoder.encode_ordinary(text))
                    all_tokens += n_tokens
                    record["tool_results"] += 1
                    call = calls[res["source_call_id"]]
                    args = call["arguments"]
                    cmd = args.get("CommandLine", "")
                    # Deliberately narrow literal inclusion, including validation failures and trial edits.
                    if not (
                        re.search(r"\banswer(?:_base|_edit)?\.FCStd", cmd, re.IGNORECASE)
                        and re.search(r"\b(?:openDocument|[a-zA-Z]+\.open)\s*\(", cmd)
                    ):
                        continue
                    starts = re.findall(r"^(?:Created At|Completed At): (\S+)", text, re.MULTILINE)
                    assert len(starts) == 2
                    bounds = tuple(map(time_value, starts))
                    assert (
                        time_value(span["started_at"]) - 1
                        <= bounds[0]
                        <= bounds[1]
                        <= time_value(span["finished_at"]) + 1
                    )
                    spans.append(bounds)
                    exit_match = re.search(r"exited with code (\d+)", text)
                    selected.append(
                        dict(
                            **base,
                            step=step["step_id"],
                            call_id=call["tool_call_id"],
                            purpose=args.get("toolAction"),
                            start=starts[0],
                            end=starts[1],
                            seconds=bounds[1] - bounds[0],
                            exit_code=int(exit_match[1]) if exit_match else None,
                            receipt_cl100k_tokens=n_tokens,
                            cmd_sha256=sha(cmd.encode()),
                            receipt_sha256=sha(text.encode()),
                        )
                    )
            record.update(
                geometry_open_calls=len(selected),
                geometry_open_union_s=union_seconds(spans),
                all_tool_text_proxy_tokens=all_tokens,
                geometry_open_proxy_tokens=sum(x["receipt_cl100k_tokens"] for x in selected),
            )
            checks.extend(selected)
        else:
            seen_files, seen_pixels = set(), set()
            image_rows = []
            for s in trace["steps"]:
                for res in s.get("observation", {}).get("results", []):
                    record["tool_results"] += 1
                    content = res["content"]
                    # Some versions of the ATIF adapter store the list as a Python literal.
                    if isinstance(content, str):
                        if not content.lstrip().startswith(("[{'type':", '[{"type":')):
                            assert "data:image/" not in content and "input_image" not in content
                            continue
                        content = ast.literal_eval(content)
                    for part in content:
                        if part["type"] != "input_image":
                            continue
                        url = part["image_url"]
                        assert url.startswith("data:image/") and ";base64," in url
                        data = base64.b64decode(url.split(",", 1)[1], validate=True)
                        sig = sha(data)
                        w, h, px = pixels(data)
                        image_rows.append(
                            dict(
                                **base,
                                step=s["step_id"],
                                call_id=res["source_call_id"],
                                bytes=len(data),
                                width=w,
                                height=h,
                                sha256=sig,
                                rgba_sha256=px,
                                repeat_file=sig in seen_files,
                                repeat_pixels=(w, h, px) in seen_pixels,
                            )
                        )
                        seen_files.add(sig)
                        seen_pixels.add((w, h, px))
            record.update(
                image_blocks=len(image_rows),
                repeat_file=sum(x["repeat_file"] for x in image_rows),
                repeat_pixels=sum(x["repeat_pixels"] for x in image_rows),
            )
            images.extend(image_rows)
        measurements.append(record)
    save(out, "v3_trials.csv", measurements)
    save(out, "v3_saved_model_checks.csv", checks)
    save(out, "v3_image_returns.csv", images)
    g = [r for r in measurements if r["agent_s"] is not None]
    seconds = sum(r["geometry_open_union_s"] for r in g)
    agent = sum(r["agent_s"] for r in g)
    nt = sum(r["geometry_open_proxy_tokens"] for r in g)
    dt = sum(r["all_tool_text_proxy_tokens"] for r in g)
    image_systems = defaultdict(list)
    for trial in measurements:
        if trial["image_blocks"] is not None:
            image_systems[trial["system"]].append(trial)
    image_summary = {}
    for system, trials in sorted(image_systems.items()):
        image_summary[system] = {
            "n": len(trials),
            "runs_with_image": sum(bool(r["image_blocks"]) for r in trials),
            "images": sum(r["image_blocks"] for r in trials),
            "repeat_file": sum(r["repeat_file"] for r in trials),
            "repeat_pixels": sum(r["repeat_pixels"] for r in trials),
        }
    return {
        "full_table_rows": len(rows),
        "geometry_open": {
            "runs": len(g),
            "runs_with_open": sum(bool(r["geometry_open_calls"]) for r in g),
            "calls": len(checks),
            "calls_nonzero_exit": sum(c["exit_code"] != 0 for c in checks),
            "union_s": seconds,
            "agent_s": agent,
            "pooled_pct": 100 * seconds / agent,
            "median_task_pct": stats.median(100 * r["geometry_open_union_s"] / r["agent_s"] for r in g),
            "tool_results": sum(r["tool_results"] for r in g),
            "raw_receipt_cl100k": nt,
            "all_receipt_cl100k": dt,
            "pooled_receipt_pct": 100 * nt / dt,
            "median_task_receipt_pct": stats.median(
                100 * r["geometry_open_proxy_tokens"] / r["all_tool_text_proxy_tokens"] for r in g
            ),
            "reported_input": sum(r["input_tokens"] for r in g),
            "reported_cached": sum(r["cached_tokens"] for r in g),
            "reported_output": sum(r["output_tokens"] for r in g),
            "reported_cost_usd": sum(r["reported_cost_usd"] for r in g),
        },
        "image_returns": image_summary,
    }


def delivered_feedback(messages):
    """Feedback in a completed model exchange. The runner indexes turns from zero."""
    final_reply = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=0)
    turn = -1
    for m in messages[:final_reply]:
        if m["role"] == "assistant":
            turn += 1
        elif m["role"] == "user" and turn >= 0:
            blocks = m["content"]
            if isinstance(blocks, str):
                blocks = [{"type": "text", "text": blocks}]
            yield turn, blocks


def feedback_receipts(feedback):
    # A stored PNG on a terminal turn may never have been shown.
    delivered = {}
    render_counts = Counter()
    for turn, blocks in feedback:
        for block in blocks:
            if block.get("type") != "text":
                continue
            heading = re.search(
                r"^### Iso render of (output\.(?:step|stl))[ \t]*\n([^\n]*)",
                block["text"],
                re.MULTILINE | re.IGNORECASE,
            )
            if heading is None:
                continue
            note = heading[2].strip()
            if note != "(see attached image below)":
                assert note.startswith(("⚠️ Render failed", "⚠️ Skipped render")), note
                render_counts["failed_or_skipped"] += 1
                render_counts["other_images"] += any(b.get("type") == "image_url" for b in blocks)
                continue
            # The runner appends manually generated PNGs first and the automatic iso last.
            assert blocks[-1].get("type") == "image_url" and turn not in delivered
            full_text = "\n".join(b.get("text", "") for b in blocks)
            execution = re.search(r"^### Execution: (FAILED|SUCCESS)", full_text, re.MULTILINE)
            delivered[turn] = (heading[1].lower(), execution[1] if execution else "UNKNOWN")
    return delivered, render_counts


def validation_replay(run, feedback, encoder):
    """Count new feedback text once per delivery, before/after a literal reference substitution."""
    section = re.compile(
        r"^### Auto-validation of (output\.(?:step|stl))[ \t]*\n(.*?)(?=\n[ \t]*\n|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    first_full = {}
    events = []
    tokens_before = tokens_after = 0
    for turn, blocks in feedback:
        for block in blocks:
            if block.get("type") != "text":
                continue
            original = block["text"]
            before = len(encoder.encode_ordinary(original))
            tokens_before += before
            tokens_after += before
            match = section.search(original)
            if match is None:
                continue
            assert section.search(original, match.end()) is None
            name, body = match.groups()
            successful = re.match(r"^Valid:[ \t]+True[ \t]*$", body, re.MULTILINE) is not None
            key = (name, body)
            previous = first_full.get(key) if successful else None
            saved = 0
            if previous is not None:
                reference = f"Same validation values as turn {previous}."
                shortened = original[: match.start(2)] + reference + original[match.end(2) :]
                saved = before - len(encoder.encode_ordinary(shortened))
                tokens_after -= saved
            elif successful:
                first_full[key] = turn
            events.append(
                {
                    "run": run,
                    "turn": turn,
                    "artifact": name,
                    "validation_sha256": sha(body.encode()),
                    "body_text_proxy_tokens": len(encoder.encode_ordinary(body)),
                    "valid_true": successful,
                    "first_full_identical_turn": previous,
                    "saved_text_proxy_tokens": saved,
                }
            )
    counts = {
        "validation_reports": len(events),
        "validation_true": sum(e["valid_true"] for e in events),
        "validation_references": sum(e["first_full_identical_turn"] is not None for e in events),
        "all_validation_body_tokens": sum(e["body_text_proxy_tokens"] for e in events),
        "repeated_valid_body_tokens": sum(
            e["body_text_proxy_tokens"] for e in events if e["first_full_identical_turn"] is not None
        ),
        "fresh_feedback_text_before": tokens_before,
        "fresh_feedback_text_after": tokens_after,
    }
    return counts, events


def public_cadgen(cache, out):
    url = f"https://huggingface.co/api/datasets/{CG_REPO}/revision/{CADGEN}?blobs=true"
    index = json.loads(fetch(cache / CADGEN / "index.json", url).read_bytes())
    assert index["sha"] == CADGEN
    files = {v["rfilename"]: v for v in index["siblings"]}
    transcripts = sorted(f for f in files if f.startswith("baselines/") and f.endswith("/conversation.json"))
    small_pngs = [f for f, r in files.items() if f.endswith("/auto_render_iso.png") and not r.get("lfs")]
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda f: hub_file(cache, CG_REPO, CADGEN, f, raw=True), transcripts))
        list(pool.map(lambda f: hub_file(cache, CG_REPO, CADGEN, f), small_pngs))

    def digest(f):
        entry = files[f]
        if entry.get("lfs"):
            return entry["lfs"]["sha256"]
        return sha(hub_file(cache, CG_REPO, CADGEN, f).read_bytes())

    artifacts = defaultdict(dict)
    for f in files:
        m = re.fullmatch(r"(baselines/.+)/turn_(\d+)/auto_render_iso\.png", f)
        if m:
            artifacts[m[1]][int(m[2])] = f
    events, runs, validation_events = [], [], []
    encoder = tiktoken.get_encoding("cl100k_base")
    text_totals = Counter()
    file_checks = []
    for f in transcripts:
        path = hub_file(cache, CG_REPO, CADGEN, f, raw=True)
        data = path.read_bytes()
        assert hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest() == files[f]["blobId"]
        run = f.rsplit("/", 1)[0]
        feedback = list(delivered_feedback(json.loads(data)))
        turns, render_counts = feedback_receipts(feedback)
        text_counts, text_events = validation_replay(run, feedback, encoder)
        validation_events.extend(text_events)
        text_totals.update(text_counts)
        validation_by_turn = {e["turn"]: e for e in text_events}
        assert len(validation_by_turn) == len(text_events)
        files_in_run = artifacts[run]
        seen, published_cache = {}, {}
        prior_pairs = {}
        count = Counter()
        for turn in sorted(turns.keys() & files_in_run.keys()):
            png = files_in_run[turn]
            image_hash = digest(png)
            previous = seen.get(image_hash)
            report = validation_by_turn[turn]
            assert report["artifact"] == turns[turn][0]
            pair = (image_hash, report["artifact"], report["validation_sha256"])
            previous_pair = prior_pairs.get(pair)
            prior_pairs[pair] = turn
            count["repeated_image_and_validation"] += previous_pair is not None
            stem = png.rsplit("/", 1)[0]
            # STEP-only counterfactual. Use a previous actually delivered view in this same run.
            step = stem + "/output.step"
            step_hash = None
            previous_source = None
            equal_pixels = None
            if turns[turn][0] == "output.step" and step in files:
                entry = files[step]
                # For sources only equality matters. Git object IDs include object type and length.
                step_hash = (
                    ("sha256:" + entry["lfs"]["sha256"]) if entry.get("lfs") else ("git:" + entry["blobId"])
                )
                previous_source = published_cache.get(step_hash)
                published_cache[step_hash] = (turn, png, image_hash)
                count["with_step"] += 1
                if previous_source:
                    count["source_hits"] += 1
                    equal_pixels = previous_source[2] == image_hash
                    if not equal_pixels:
                        old_data = hub_file(cache, CG_REPO, CADGEN, previous_source[1]).read_bytes()
                        new_data = hub_file(cache, CG_REPO, CADGEN, png).read_bytes()
                        assert sha(old_data) == previous_source[2] and sha(new_data) == image_hash
                        equal_pixels = pixels(old_data) == pixels(new_data)
                    count["source_hit_file_matches"] += previous_source[2] == image_hash
                    count["source_hit_pixel_matches"] += equal_pixels
            seen[image_hash] = turn
            count["repeats"] += previous is not None
            count["delivered"] += 1
            events.append(
                {
                    "run": run,
                    "fixture": run.rsplit("/", 1)[1],
                    "turn": turn,
                    "execution_status": turns[turn][1],
                    "artifact": png,
                    "image_sha256": image_hash,
                    "previous_identical_turn": previous,
                    "validation_sha256": report["validation_sha256"],
                    "previous_identical_image_and_validation_turn": previous_pair,
                    "source_id": step_hash,
                    "previously_seen_source_turn": previous_source[0] if previous_source else None,
                    "source_hit_same_pixels": equal_pixels,
                }
            )
            if not file_checks and previous is not None:
                earlier = files_in_run[previous]
                for which in (earlier, png):
                    assert sha(hub_file(cache, CG_REPO, CADGEN, which).read_bytes()) == image_hash
                    file_checks.append(which)
        runs.append(
            dict(
                run=run,
                release_group=run.split("/")[1],
                model=run.split("/")[2],
                fixture=run.rsplit("/", 1)[1],
                conversation_sha256=sha(data),
                stored_iso_pngs=len(files_in_run),
                eligible_feedback=len(turns),
                failed_or_skipped_auto_render=render_counts["failed_or_skipped"],
                failed_auto_render_with_other_images=render_counts["other_images"],
                matched=count["delivered"],
                unmatched_receipts=len(turns.keys() - files_in_run.keys()),
                repeats=count["repeats"],
                repeated_image_and_validation=count["repeated_image_and_validation"],
                paired_step=count["with_step"],
                source_hits=count["source_hits"],
                source_hit_file_matches=count["source_hit_file_matches"],
                source_hit_pixel_matches=count["source_hit_pixel_matches"],
                **text_counts,
            )
        )
    save(out, "cadgen_feedback.csv", events)
    save(out, "cadgen_validation.csv", validation_events)
    save(out, "cadgen_runs.csv", runs)
    model_rows = []
    for model in sorted({r["model"] for r in runs}):
        sample = [r for r in runs if r["model"] == model]
        matched = sum(r["matched"] for r in sample)
        repeated = sum(r["repeats"] for r in sample)
        model_rows.append(
            {
                "model": model,
                "runs": len(sample),
                "fixtures": len({r["fixture"] for r in sample}),
                "matched": matched,
                "repeated": repeated,
                "repeat_pct": 100 * repeated / matched if matched else None,
            }
        )
    save(out, "cadgen_by_model.csv", model_rows)
    observed = [r for r in runs if r["matched"]]
    d = {
        key: sum(r[key] for r in runs)
        for key in [
            "stored_iso_pngs",
            "eligible_feedback",
            "failed_or_skipped_auto_render",
            "failed_auto_render_with_other_images",
            "matched",
            "unmatched_receipts",
            "repeats",
            "repeated_image_and_validation",
            "paired_step",
            "source_hits",
            "source_hit_file_matches",
            "source_hit_pixel_matches",
        ]
    }
    # Sensitivity to fixture selection, keeping all model/run rows for a fixture together.
    by_fixture = defaultdict(lambda: [0, 0])
    for r in runs:
        bucket = by_fixture[r["fixture"]]
        bucket[0] += r["repeats"]
        bucket[1] += r["matched"]
    rng = random.Random(20261120)
    blocks = [pair for _, pair in sorted(by_fixture.items())]
    ratios = []
    for _ in range(5000):
        sample = rng.choices(blocks, k=len(blocks))
        ratios.append(100 * sum(a for a, _ in sample) / sum(b for _, b in sample))
    ratios.sort()
    clean = [r for r in runs if not r["failed_auto_render_with_other_images"]]
    status_counts = defaultdict(lambda: {"images": 0, "repeats": 0})
    adjacent = 0
    for event in events:
        previous = event["previous_identical_turn"]
        counts = status_counts[event["execution_status"]]
        counts["images"] += 1
        counts["repeats"] += previous is not None
        adjacent += previous is not None and previous + 1 == event["turn"]
    d["execution_status_slices"] = dict(status_counts)
    d["repeats_on_consecutive_turns"] = adjacent
    unique_runs = {}
    for row in runs:
        unique_runs.setdefault(row["conversation_sha256"], row)
    d["unique_transcripts"] = len(unique_runs)
    d["deduplicated_transcripts_sensitivity"] = [
        sum(r["repeats"] for r in unique_runs.values()),
        sum(r["matched"] for r in unique_runs.values()),
    ]
    before = text_totals["fresh_feedback_text_before"]
    after = text_totals["fresh_feedback_text_after"]
    text_totals["saved_pct"] = 100 * (before - after) / before
    text_totals["repeated_body_pct_of_fresh_feedback_text"] = (
        100 * text_totals["repeated_valid_body_tokens"] / before
    )
    d["validation_text_replay"] = dict(text_totals)
    d.update(
        runs=len(runs),
        fixtures=len(blocks),
        runs_with_matched=len(observed),
        runs_with_repeat=sum(bool(r["repeats"]) for r in runs),
        repeat_pct=100 * d["repeats"] / d["matched"],
        equal_run_mean_pct=stats.mean(100 * r["repeats"] / r["matched"] for r in observed),
        median_observed_run_pct=stats.median(100 * r["repeats"] / r["matched"] for r in observed),
        fixture_cluster_percentile95_pct=[ratios[124], ratios[4874]],
        runs_without_other_images_after_failed_auto_render_sensitivity=[
            sum(r["repeats"] for r in clean),
            sum(r["matched"] for r in clean),
        ],
        first_duplicate_verified_by_download=file_checks,
    )
    return d


def assembly_materials(cache, out):
    repo = "Zihan1004/CADWORLD"
    source = hub_file(cache, repo, WORLD, "data/tasks.jsonl")
    rows = [json.loads(line) for line in source.read_bytes().splitlines()]
    seed = out / "cadworld_seed"
    seed.mkdir(exist_ok=True)
    inventory = []
    for task in rows:
        if task["domain"] != "assemble":
            continue
        native = json.loads(task["raw_task"])
        rules = native["evaluator"]["expected"]["rules"]["assembly_joint_counts"]
        planned = native["assembly_evaluation_plan"]
        chosen = task["task_id"] in ("freecad-assemble-003", "freecad-assemble-016", "freecad-assemble-005")
        inventory.append(
            {
                "task": task["task_id"],
                "components_in_reference_plan": planned["expected_component_count"],
                "native_joint_count_rules": json.dumps(rules, sort_keys=True),
                "spec": task["task_json_path"],
                "initial": task["precondition_path"],
                "reference": task["reference_solution_path"],
                "downloaded_seed": chosen,
            }
        )
        if chosen:
            names = [
                task["task_json_path"],
                task["precondition_path"],
                task["reference_solution_path"],
                *task["instruction_images"],
            ]
            for name in names:
                src = hub_file(cache, repo, WORLD, name)
                dest = seed / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if src.suffix == ".FCStd":
                    with zipfile.ZipFile(src) as archive:
                        assert "Document.xml" in archive.namelist() and archive.testzip() is None
                elif src.suffix == ".png":
                    with Image.open(src) as img:
                        img.verify()
                shutil.copyfile(src, dest)
    assert len(inventory) == 25
    shutil.copyfile(hub_file(cache, repo, WORLD, "LICENSE"), seed / "LICENSE")
    save(out, "cadworld_tasks.csv", inventory)
    return {
        "tasks": len(inventory),
        "seeds": [r["task"] for r in inventory if r["downloaded_seed"]],
        "native_assembly_documents_checked": 6,
        "revision": WORLD,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home() / "Library/Caches/dbcad-observation-study")
    parser.add_argument("--out", type=Path, default=HERE)
    parser.add_argument(
        "--funcad", type=Path, default=HERE.parents[1] / "DBCAD-mac6/doc/literature-review/funcad.pdf"
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = paper_tables(args.cache, args.out, args.funcad)
    result["Parametric_CAD_Bench_v3"] = public_v3(args.cache, args.out)
    result["CADGenBench"] = public_cadgen(args.cache, args.out)
    result["CADWorld"] = assembly_materials(args.cache, args.out)
    (args.out / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
