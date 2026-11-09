"""Build a review video with the OSWorld desktop and WorkSwarm event console.

The raw desktop.mp4 remains untouched for evaluation. The side panel is built
from the exact request and saved SSE events, so no terminal covers the agent's
desktop or changes its available tools.
"""

from __future__ import annotations

import argparse
from collections import deque
import json
from pathlib import Path
import subprocess


PANEL_WIDTH = 576


def probe_video(path: Path) -> tuple[int, int, float]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    info = json.loads(result.stdout)
    video = next(stream for stream in info["streams"] if "width" in stream)
    return int(video["width"]), int(video["height"]), float(info["format"]["duration"])


def request_prompt(output: Path) -> str:
    request = output / "request.json"
    if request.exists():
        return str(json.loads(request.read_text(encoding="utf-8"))["query"])
    history = output / "history.jsonl"
    if history.exists():
        for line in history.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("role") == "user":
                return str(item.get("content", ""))
    raise RuntimeError("No original WorkSwarm prompt found")


def video_start_epoch(output: Path) -> float:
    timing = output / "recording.json"
    if timing.exists():
        return float(json.loads(timing.read_text(encoding="utf-8"))["requested_at"]) + 0.5
    # Older probes did not write recording.json; baseline.json was written
    # immediately before starting ffmpeg. The alignment is approximate.
    return (output / "baseline.json").stat().st_mtime + 0.5


def event_time(payload: dict) -> float | None:
    if isinstance(payload.get("timestamp"), (int, float)):
        return float(payload["timestamp"]) / 1000
    if isinstance(payload.get("llm_start_ts"), (int, float)):
        return float(payload["llm_start_ts"])
    if isinstance(payload.get("at_ms"), (int, float)):
        return float(payload["at_ms"]) / 1000
    return None


def event_label(payload: dict) -> str | None:
    kind = payload.get("event_type")
    if kind == "chat.llm_call_start":
        return "主 Agent 调用模型"
    if kind == "chat.llm_call_end":
        return "主 Agent 模型调用完成"
    if kind == "chat.tool_call":
        tool = payload.get("tool_call") or {}
        return f"调用工具：{tool.get('name', 'unknown')}"
    if kind == "chat.tool_result":
        return f"工具返回：{payload.get('tool_name', 'unknown')}"
    if kind == "chat.subtask_update":
        return f"{payload.get('display_name') or payload.get('subagent_type') or '子 Agent'}：{payload.get('status', '更新')}"
    if kind == "chat.subagent_activity":
        summary = str(payload.get("summary") or "").replace("\n", " ").strip()
        return f"子 Agent {payload.get('kind', '活动')}：{summary[:44]}" if summary else f"子 Agent：{payload.get('kind', '活动')}"
    if kind == "chat.final":
        return "WorkSwarm 已完成回答"
    if kind in {"chat.error", "runtime.error"}:
        return "WorkSwarm 报告错误"
    return None


def ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def visible_width(text: str) -> int:
    return sum(2 if ord(char) > 127 else 1 for char in text)


def wrap_text(text: str, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text.replace("\n", " "):
        if visible_width(current + char) > width:
            lines.append(current)
            current = ""
            if len(lines) == max_lines - 1:
                break
        current += char
    if current:
        lines.append(current)
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1][:-1] + "…"
    return lines[:max_lines]


def ass_escape(value: str) -> str:
    return value.replace("\\", "／").replace("{", "(").replace("}", ")")


def dialogue(start: float, end: float, style: str, text: str) -> str:
    return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}"


def make_review_video(output: Path) -> Path:
    output = output.resolve()
    source = output / "desktop.mp4"
    width, height, duration = probe_video(source)
    started = video_start_epoch(output)
    prompt = request_prompt(output)
    if not (output / "request.json").exists():
        (output / "request.json").write_text(json.dumps({
            "query": prompt,
            "session_id": json.loads((output / "session.json").read_text(encoding="utf-8"))["data"]["session_id"],
            "source": "history.jsonl",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    events: list[tuple[float, str]] = []
    for line in (output / "stream.sse").read_text(encoding="utf-8").splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:]).get("payload") or {}
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        when, label = event_time(payload), event_label(payload)
        if when is None or label is None:
            continue
        offset = when - started
        if 0 <= offset < duration:
            if events and label == events[-1][1] and offset - events[-1][0] < 60:
                continue
            events.append((offset, label))
    events.sort()

    left = width + 24
    total_width = width + PANEL_WIDTH
    ass = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {total_width}",
        f"PlayResY: {height}", "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Header,Microsoft YaHei,25,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "Style: Body,Microsoft YaHei,17,&H00E8E8E8,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "Style: Prompt,Microsoft YaHei,16,&H006EE8FF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        dialogue(0, duration, "Header", f"{{\\pos({left},32)}}WorkSwarm 运行记录"),
        dialogue(0, duration, "Body", f"{{\\pos({left},82)}}输入 Prompt（原文）"),
        dialogue(0, duration, "Prompt", f"{{\\pos({left},115)}}" + r"\N".join(ass_escape(s) for s in wrap_text(prompt, 56, 10))),
        dialogue(0, duration, "Body", f"{{\\pos({left},708)}}原始记录：request.json / stream.sse / debug-trace.txt"),
    ]
    recent: deque[str] = deque(maxlen=9)
    previous = 0.0
    for index, (offset, label) in enumerate(events):
        if offset > previous:
            panel = r"\N".join(ass_escape(s) for s in recent) if recent else "等待 Agent 事件…"
            ass.append(dialogue(previous, offset, "Body", f"{{\\pos({left},350)}}{panel}"))
        recent.append(f"{ass_time(offset)}  {label}")
        previous = offset
    panel = r"\N".join(ass_escape(s) for s in recent) if recent else "等待 Agent 事件…"
    ass.append(dialogue(previous, duration, "Body", f"{{\\pos({left},350)}}{panel}"))
    subtitle = output / "review.ass"
    subtitle.write_text("\n".join(ass) + "\n", encoding="utf-8")
    target = output / "review.mp4"
    temporary = output / "review.tmp.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-vf", f"pad=iw+{PANEL_WIDTH}:ih:0:0:color=0x101820,ass={subtitle.name}",
         "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-pix_fmt", "yuv420p", "-profile:v", "main", "-movflags", "+faststart",
         str(temporary)], cwd=output, check=True, timeout=max(300, int(duration * 3)),
    )
    temporary.replace(target)
    probe_video(target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(make_review_video(arguments.output))
