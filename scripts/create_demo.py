#!/usr/bin/env python3
"""Create the Ops Factory product demo GIF and documentation assets.

The demo is intentionally deterministic: it records the implemented workflow
contract as a polished product walkthrough without depending on browser
recording, cluster availability, or a local model server already being warm.
It is safe to rerun and overwrites only files under docs/demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "demo"
GIF_PATH = OUT_DIR / "opsfactory-demo.gif"
POSTER_PATH = OUT_DIR / "opsfactory-demo-poster.png"
STORYBOARD_PATH = OUT_DIR / "opsfactory-demo-storyboard.md"
FEATURE_MAP_PATH = OUT_DIR / "opsfactory-feature-map.md"

W, H = 1280, 720
FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

NAV_ITEMS = [
    "运维流程",
    "数据平台",
    "运维问诊台",
    "根因分析",
    "持续守护",
    "故障数据收集",
    "模型交互",
    "成效看板",
]

COLORS = {
    "ink": "#0b1220",
    "ink2": "#172033",
    "muted": "#64748b",
    "line": "#d9e2ef",
    "panel": "#ffffff",
    "soft": "#f5f8fc",
    "teal": "#0f766e",
    "blue": "#2563eb",
    "indigo": "#4f46e5",
    "violet": "#7c3aed",
    "green": "#16a34a",
    "amber": "#d97706",
    "rose": "#e11d48",
    "cyan": "#0891b2",
    "dark": "#08111f",
}


def load_font(size: int, index: int = 0):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=index)
            except Exception:
                continue
    return ImageFont.load_default()


F_HERO = load_font(48)
F_TITLE = load_font(34)
F_H2 = load_font(25)
F_H3 = load_font(20)
F_BODY = load_font(17)
F_SMALL = load_font(13)
F_TINY = load_font(11)
F_MONO = load_font(14)


@dataclass(frozen=True)
class DemoScene:
    title: str
    focus: str
    talk_track: str


SCENES: list[DemoScene] = [
    DemoScene("产品级开场", "定位与价值", "Ops Factory 是本地优先的 AIOps / SRE 智能运维闭环平台。"),
    DemoScene("零基础一键安装", "没有 Python 的机器", "脚本自动准备私有 Python、虚拟环境、依赖、本地模型和 K8s 工具。"),
    DemoScene("一键启动与重启", "稳定启动入口", "start_opsfactory.sh 负责补齐环境、回收旧进程、输出访问地址和日志路径。"),
    DemoScene("模型治理边界", "默认本地 Qwen-0.6B", "系统不内置外部 API；用户不选择 API 时所有模型能力都走本地 Qwen-0.6B。"),
    DemoScene("模型来源弹窗", "用户自带 API", "用户主动选择时支持 OpenAI-compatible 和 Anthropic-compatible 参数。"),
    DemoScene("运维流程首页", "诊断飞行台", "首页展示数据、证据、Agent、恢复和学习的一条闭环。"),
    DemoScene("数据平台总入口", "三类数据源", "Cloud-OpsBench、实时故障注入、企业自定义数据统一进入 RCA。"),
    DemoScene("静态故障案例", "Cloud-OpsBench", "搜索和选择离线案例，加载日志、链路、指标、告警和拓扑。"),
    DemoScene("动态故障注入", "真实 Kubernetes", "选择平台、故障、目标服务、注入时间、持续时间和采样窗口。"),
    DemoScene("企业自定义数据", "内部数据接入", "注册企业 JSON、根因服务、接口结构和自定义证据模态。"),
    DemoScene("3D 传播图", "系统拓扑", "用根因服务、受影响服务、依赖边和风险强度展示故障传播。"),
    DemoScene("多模态证据", "Log Trace Metric Alert", "RCA 前展示原始证据，并允许人工核验数据是否完整。"),
    DemoScene("工具调用预案", "Human Confirm", "Agent 会解释为什么调用工具、需要什么输入、预期产物是什么。"),
    DemoScene("运维问诊台", "自然语言问诊", "非专家可以直接围绕当前故障提问，答案绑定当前 case 证据。"),
    DemoScene("RCA 路径中心", "三条诊断路径", "多智能体、Hermes RCA Agent、企业 RCA 流程并列选择。"),
    DemoScene("多智能体接力", "Graph Orchestrated", "SOP、上下文、记忆、工具路由、证据、诊断、学习逐步执行。"),
    DemoScene("Agent 实时日志", "可审计执行", "每一步都有状态、输入、输出和人工确认点。"),
    DemoScene("Hermes RCA Agent", "独立智能体路径", "上下文胶囊、记忆检索、工具路由和失败学习独立运行。"),
    DemoScene("企业 RCA 流程", "接入内部算法", "可注册 graph_rca、runbook、internal_pipeline 或 MCP 工具。"),
    DemoScene("RCA 结果面板", "Top-K 与评估", "展示 LLM 使用情况、候选根因、ACC@K、MRR 和 Ground Truth。"),
    DemoScene("报告与恢复", "交付闭环", "生成 PDF 诊断报告，并回查 Kubernetes 恢复是否真实完成。"),
    DemoScene("模型交互台", "复盘与排查计划", "围绕 RCA、拓扑和 Agent 能力做解释、质疑、导出和会话管理。"),
    DemoScene("会话与上下文", "Human-in-the-loop", "内置快捷问题：复盘最近 RCA、分析自进化、生成排查计划。"),
    DemoScene("持续守护对象", "真实系统或模拟系统", "接入真实端口或一键接入内置可观测模拟系统。"),
    DemoScene("持续守护场景", "15 类风险", "覆盖延迟、错误率、资源压力、锁等待、流量突增、配置漂移等场景。"),
    DemoScene("守护计划报告", "风险与权限边界", "巡检、RCA 预热、报告沉淀默认只读，高风险动作人工确认。"),
    DemoScene("故障数据收集", "SFT RL Eval", "持续注入并采集训练、偏好、评估和企业复盘数据。"),
    DemoScene("采集结果预览", "可用样本", "查看任务摘要、错误、格式和 JSON 预览。"),
    DemoScene("成效看板", "系统是否变好", "观察累计运行、成功率、平均 MRR、LLM 使用率和趋势。"),
    DemoScene("失败学习闭环", "Harness vNext", "失败 case 进入归因、Prompt 补丁、离线 Replay 和发布门禁。"),
    DemoScene("交付验收收束", "可演示可复现", "安装、启动、模型边界、功能覆盖和 README 交付全部闭环。"),
]


def rounded(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: str,
    outline: str | None = None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def text_width(draw: ImageDraw.ImageDraw, value: str, fnt: ImageFont.ImageFont) -> float:
    return draw.textlength(value, font=fnt)


def wrap_text(draw: ImageDraw.ImageDraw, value: str, width: int, fnt, max_lines: int = 8) -> list[str]:
    lines: list[str] = []
    for paragraph in value.split("\n"):
        current = ""
        tokens: Iterable[str]
        if " " in paragraph:
            tokens = [token + " " for token in paragraph.split(" ")]
        else:
            tokens = paragraph
        for raw_token in tokens:
            token_text = str(raw_token)
            pieces: Iterable[str]
            if text_width(draw, token_text, fnt) > width:
                pieces = token_text
            else:
                pieces = [token_text]
            for token in pieces:
                trial = current + token
                if text_width(draw, trial, fnt) <= width:
                    current = trial
                else:
                    if current:
                        lines.append(current.rstrip())
                    current = str(token).strip()
                    if len(lines) >= max_lines:
                        return lines[:max_lines]
        if current:
            lines.append(current.rstrip())
        if len(lines) >= max_lines:
            return lines[:max_lines]
    return lines[:max_lines]


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: str,
    fnt=F_BODY,
    fill: str = COLORS["ink2"],
    width: int | None = None,
    line_gap: int = 6,
    max_lines: int = 8,
) -> int:
    x, y = xy
    if width:
        for line in wrap_text(draw, value, width, fnt, max_lines=max_lines):
            draw.text((x, y), line, font=fnt, fill=fill)
            y += getattr(fnt, "size", 15) + line_gap
        return y
    draw.text((x, y), value, font=fnt, fill=fill)
    return y + getattr(fnt, "size", 15) + line_gap


def badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, fill="#e8f5ff", stroke="#b8d7ff", color="#1d4ed8") -> tuple[int, int, int, int]:
    x, y = xy
    pad_x = 13
    w = int(text_width(draw, value, F_SMALL)) + pad_x * 2
    rounded(draw, (x, y, x + w, y + 28), 14, fill, stroke)
    draw.text((x + pad_x, y + 7), value, font=F_SMALL, fill=color)
    return (x, y, x + w, y + 28)


def metric_tile(draw: ImageDraw.ImageDraw, box, label: str, value: str, accent: str) -> None:
    x1, y1, x2, y2 = box
    rounded(draw, box, 10, "#ffffff", COLORS["line"])
    rounded(draw, (x1 + 12, y1 + 12, x1 + 18, y2 - 12), 4, accent)
    draw.text((x1 + 30, y1 + 16), label, font=F_SMALL, fill=COLORS["muted"])
    draw.text((x1 + 30, y1 + 42), value, font=F_H2, fill=COLORS["ink"])


def card(draw: ImageDraw.ImageDraw, box, title: str, body: str, accent: str = COLORS["blue"], compact: bool = False) -> None:
    x1, y1, x2, y2 = box
    rounded(draw, box, 12, "#ffffff", COLORS["line"])
    rounded(draw, (x1 + 18, y1 + 18, x1 + 24, y2 - 18), 4, accent)
    draw.text((x1 + 38, y1 + 18), title, font=F_H3 if compact else F_H2, fill=COLORS["ink"])
    draw_text(
        draw,
        (x1 + 38, y1 + 50 if compact else y1 + 60),
        body,
        F_SMALL if compact else F_BODY,
        COLORS["muted"],
        width=x2 - x1 - 62,
        line_gap=5,
        max_lines=5 if compact else 6,
    )


def dark_gradient() -> Image.Image:
    img = Image.new("RGB", (W, H), COLORS["dark"])
    draw = ImageDraw.Draw(img)
    for y in range(H):
        ratio = y / H
        r = int(7 + ratio * 12)
        g = int(16 + ratio * 20)
        b = int(31 + ratio * 24)
        draw.line((0, y, W, y), fill=(r, g, b))
    for offset in range(-420, W, 110):
        draw.line((offset, H, offset + 460, 0), fill="#0f2840", width=1)
    for y in range(90, H, 78):
        draw.line((0, y, W, y), fill="#0d2135", width=1)
    draw.polygon([(860, 0), (W, 0), (W, 236), (1010, 188)], fill="#071221")
    draw.polygon([(0, 602), (430, 678), (W, 606), (W, H), (0, H)], fill="#071725")
    return img


def page_frame(title: str, subtitle: str, idx: int, active: str | None = None):
    img = Image.new("RGB", (W, H), "#edf3f8")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, W, 132), fill="#0b1220")
    draw.rectangle((0, 130, W, 136), fill="#14b8a6")
    draw.text((42, 28), "Ops Factory", font=F_TITLE, fill="#ffffff")
    badge(draw, (42, 76), "FULL PRODUCT DEMO", "#12233a", "#244866", "#b8fff2")
    badge(draw, (232, 76), "LOCAL-FIRST QWEN-0.6B", "#102f2d", "#1f766e", "#a7f3d0")
    draw.text((430, 28), title, font=F_TITLE, fill="#ffffff")
    draw_text(draw, (432, 73), subtitle, F_SMALL, "#c7d2fe", width=620, max_lines=2)
    rounded(draw, (1096, 34, 1238, 76), 21, "#11253c", "#2dd4bf")
    draw.text((1120, 47), f"{idx:02}/{len(SCENES):02}", font=F_H3, fill="#e6fffb")

    shell = draw_shell(draw, active or "运维流程")
    return img, draw, shell


def draw_shell(draw: ImageDraw.ImageDraw, active: str) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = 34, 158, 1246, 686
    rounded(draw, (x1 + 6, y1 + 8, x2 + 6, y2 + 8), 18, "#cdd7e5")
    rounded(draw, (x1, y1, x2, y2), 18, "#ffffff", "#cbd5e1")
    draw.rectangle((x1, y1 + 48, x2, y1 + 49), fill="#e2e8f0")
    draw.ellipse((x1 + 20, y1 + 19, x1 + 32, y1 + 31), fill="#f43f5e")
    draw.ellipse((x1 + 42, y1 + 19, x1 + 54, y1 + 31), fill="#f59e0b")
    draw.ellipse((x1 + 64, y1 + 19, x1 + 76, y1 + 31), fill="#22c55e")
    rounded(draw, (x1 + 100, y1 + 13, x1 + 520, y1 + 37), 12, "#f1f5f9", "#e2e8f0")
    draw.text((x1 + 118, y1 + 19), "http://127.0.0.1:8080", font=F_TINY, fill="#475569")
    badge(draw, (x2 - 385, y1 + 11), "模型来源: 本地 Qwen-0.6B", "#ecfeff", "#99f6e4", "#0f766e")
    badge(draw, (x2 - 164, y1 + 11), "5s refresh", "#f8fafc", "#dbe3ef", "#475569")

    nav_x, nav_y, nav_w = x1 + 18, y1 + 66, 190
    rounded(draw, (nav_x, nav_y, nav_x + nav_w, y2 - 18), 14, "#0f172a")
    draw.text((nav_x + 20, nav_y + 20), "COMMAND", font=F_SMALL, fill="#94a3b8")
    y = nav_y + 56
    for item in NAV_ITEMS:
        selected = item == active
        rounded(draw, (nav_x + 14, y, nav_x + nav_w - 14, y + 38), 10, "#1d4ed8" if selected else "#0f172a")
        draw.text((nav_x + 28, y + 10), item, font=F_SMALL, fill="#ffffff" if selected else "#cbd5e1")
        y += 48
    return (x1 + 232, y1 + 72, x2 - 24, y2 - 24)


def section_header(draw: ImageDraw.ImageDraw, area, eyebrow: str, heading: str, note: str) -> int:
    x, y, w, _ = area
    badge(draw, (x, y), eyebrow, "#eff6ff", "#bfdbfe", COLORS["blue"])
    draw.text((x, y + 42), heading, font=F_TITLE, fill=COLORS["ink"])
    return draw_text(draw, (x, y + 88), note, F_BODY, COLORS["muted"], width=w, max_lines=2)


def draw_pipeline(
    draw: ImageDraw.ImageDraw,
    y: int,
    labels: Sequence[str],
    colors: Sequence[str],
    x1=88,
    x2=1190,
    label_color: str = "#dbeafe",
) -> None:
    gap = (x2 - x1) // (len(labels) - 1)
    for idx, label in enumerate(labels):
        x = x1 + idx * gap
        if idx:
            prev = x1 + (idx - 1) * gap
            draw.line((prev + 46, y, x - 46, y), fill="#6ee7b7", width=4)
            draw.polygon([(x - 46, y), (x - 58, y - 7), (x - 58, y + 7)], fill="#6ee7b7")
        rounded(draw, (x - 48, y - 48, x + 48, y + 48), 24, "#ffffff", colors[idx], 2)
        draw.text((x - text_width(draw, f"{idx+1:02}", F_H2) / 2, y - 20), f"{idx+1:02}", font=F_H2, fill=colors[idx])
        draw_text(draw, (x - 72, y + 66), label, F_SMALL, label_color, width=144, max_lines=2)


def frame_cover(idx: int) -> Image.Image:
    img = dark_gradient()
    draw = ImageDraw.Draw(img)
    badge(draw, (62, 52), "OPS FACTORY v5.0", "#102a43", "#1e6f8f", "#c7f9ff")
    badge(draw, (242, 52), "AIOps / SRE Command Center", "#14342f", "#1f766e", "#bbf7d0")
    draw.text((62, 116), "Ops Factory", font=F_HERO, fill="#ffffff")
    draw_text(
        draw,
        (65, 178),
        "本地优先的大模型智能运维闭环平台：从故障数据、证据、Agent RCA 到恢复校验、报告和持续学习，一次演示覆盖完整交付链路。",
        F_H2,
        "#d7e4ff",
        width=720,
        max_lines=3,
    )
    draw_pipeline(
        draw,
        404,
        ["数据接入", "证据融合", "Agent RCA", "恢复校验", "成效学习"],
        [COLORS["teal"], COLORS["blue"], COLORS["violet"], COLORS["green"], COLORS["amber"]],
    )
    rounded(draw, (844, 92, 1194, 304), 18, "#ffffff", "#a5f3fc")
    draw.text((878, 126), "默认模型边界", font=F_H2, fill=COLORS["ink"])
    draw_text(
        draw,
        (878, 174),
        "系统不内置外部 API。用户不配置自己的 API 时，所有需要大模型的地方默认且只走本地 Qwen-0.6B。",
        F_BODY,
        COLORS["muted"],
        width=248,
        max_lines=5,
    )
    rounded(draw, (844, 522, 1194, 636), 18, "#0f172a", "#2dd4bf")
    draw.text((876, 550), "Demo Coverage", font=F_H3, fill="#ffffff")
    draw_text(draw, (876, 582), "31 个镜头覆盖 8 个主导航、3 条 RCA 路径、15 类守护场景和一键安装启动。", F_SMALL, "#bae6fd", width=270, max_lines=3)
    return img


def frame_install(idx: int) -> Image.Image:
    img, draw, area = page_frame("零基础也能一键安装", "没有 Python 的电脑也能完成环境准备。", idx, "运维流程")
    x, y, w, _ = area
    section_header(draw, area, "ONE-CLICK SETUP", "从空机器到可运行平台", "setup_opsfactory_env.sh 会自动准备私有 Python、虚拟环境、依赖、本地模型和 K8s 工具。")
    rounded(draw, (x, y + 150, x + w, y + 382), 14, "#0b1220", "#1e293b")
    lines = [
        "$ bash scripts/setup_opsfactory_env.sh",
        "[Ops Factory] No Python 3.10+ found -> install .opsfactory/python",
        "[Ops Factory] Creating .venv and upgrading pip / wheel",
        "[Ops Factory] Installing dashboard, kubernetes, transformers, torch",
        "[Ops Factory] Downloading Qwen/Qwen3-0.6B into models/Qwen/Qwen3-0.6B",
        "[Ops Factory] Installing local kubectl and kind into .opsfactory/bin",
        "[Ops Factory] Writing .opsfactory/env.sh and SRE/.env",
    ]
    ty = y + 176
    for line in lines:
        fill = "#bbf7d0" if line.startswith("$") else "#dbeafe"
        draw.text((x + 28, ty), line, font=F_MONO, fill=fill)
        ty += 28
    metric_tile(draw, (x, y + 402, x + 214, y + 486), "Python", "自动补齐", COLORS["green"])
    metric_tile(draw, (x + 235, y + 402, x + 449, y + 486), "Model", "Qwen-0.6B", COLORS["teal"])
    metric_tile(draw, (x + 470, y + 402, x + 684, y + 486), "K8s tools", "kubectl/kind", COLORS["blue"])
    metric_tile(draw, (x + 705, y + 402, x + 919, y + 486), "Mode", "zero setup", COLORS["amber"])
    return img


def frame_launch(idx: int) -> Image.Image:
    img, draw, area = page_frame("一键启动与重启", "启动脚本会检查依赖、回收旧进程、写入日志，并给出访问地址。", idx, "运维流程")
    x, y, w, _ = area
    section_header(draw, area, "START SCRIPT", "一个命令启动 Dashboard", "适合本机、局域网、后台、tmux、重启和停止等场景。")
    card(draw, (x, y + 145, x + 292, y + 328), "前台启动", "bash scripts/start_opsfactory.sh --host 127.0.0.1 --port 8080", COLORS["teal"], compact=True)
    card(draw, (x + 314, y + 145, x + 606, y + 328), "后台启动", "bash start_opsfactory.sh --background --restart --port 8080", COLORS["blue"], compact=True)
    card(draw, (x + 628, y + 145, x + 920, y + 328), "沙箱/远程", "bash start_opsfactory.sh --tmux --restart --port 8080", COLORS["violet"], compact=True)
    rounded(draw, (x, y + 350, x + w, y + 490), 14, "#0b1220")
    draw.text((x + 30, y + 377), "Dashboard: http://127.0.0.1:8080", font=F_MONO, fill="#bbf7d0")
    draw.text((x + 30, y + 413), "Logs: /SRE/logs/opsfactory-8080.log", font=F_MONO, fill="#dbeafe")
    draw.text((x + 30, y + 449), "KUBECONFIG=<not set>  OPSFACTORY_KUBECTL=<system kubectl>", font=F_MONO, fill="#dbeafe")
    return img


def frame_model_boundary(idx: int) -> Image.Image:
    img, draw, area = page_frame("模型治理边界", "系统默认本地模型；API 只由用户主动提供。", idx, "模型交互")
    x, y, w, _ = area
    section_header(draw, area, "MODEL GOVERNANCE", "默认且只走本地 Qwen-0.6B", "Ops Factory 不内置外部 API Key，不会默认调用外部模型。")
    card(draw, (x, y + 146, x + 292, y + 382), "默认路径", "RCA、Hermes、告警压缩、模型交互等所有模型能力默认连接本地 Qwen-0.6B 服务。", COLORS["teal"])
    card(draw, (x + 314, y + 146, x + 606, y + 382), "用户自带 API", "只有用户点击“模型来源”并填写自己的参数，才切换到 OpenAI-compatible 或 Anthropic-compatible。", COLORS["blue"])
    card(draw, (x + 628, y + 146, x + 920, y + 382), "安全约束", "用户 API Key 不写入仓库文件；默认重启后回到本地模式，除非用户自行设置环境变量。", COLORS["amber"])
    draw_pipeline(
        draw,
        y + 460,
        ["Local first", "User choice", "Runtime only", "Back to local"],
        [COLORS["teal"], COLORS["blue"], COLORS["amber"], COLORS["green"]],
        x1=x + 90,
        x2=x + 830,
        label_color=COLORS["muted"],
    )
    return img


def frame_model_modal(idx: int) -> Image.Image:
    img, draw, area = page_frame("模型来源弹窗", "把“系统默认本地”和“用户自带 API”明确分开。", idx, "模型交互")
    x, y, w, _ = area
    rounded(draw, (x + 118, y + 28, x + 802, y + 494), 18, "#ffffff", "#94a3b8", 2)
    draw.text((x + 154, y + 66), "模型来源", font=F_TITLE, fill=COLORS["ink"])
    draw_text(draw, (x + 154, y + 112), "默认使用随项目部署的本地 Qwen-0.6B。Ops Factory 不内置任何外部 API。", F_BODY, COLORS["muted"], width=560)
    options = [
        ("本地 Qwen-0.6B", "推荐默认项，不需要 API Key，所有模型能力走本机模型服务。", True, COLORS["teal"]),
        ("用户自带 OpenAI-compatible API", "填写你自己的 Base URL、模型名和 Key；系统不会提供或保存外部 API。", False, COLORS["blue"]),
        ("用户自带 Anthropic-compatible API", "填写 Anthropic 格式 endpoint、模型名和 Key。", False, COLORS["violet"]),
    ]
    oy = y + 178
    for title, body, selected, accent in options:
        rounded(draw, (x + 154, oy, x + 766, oy + 74), 12, "#f0fdfa" if selected else "#ffffff", accent if selected else COLORS["line"], 2 if selected else 1)
        draw.ellipse((x + 176, oy + 25, x + 198, oy + 47), fill=accent if selected else "#ffffff", outline=accent, width=2)
        draw.text((x + 216, oy + 16), title, font=F_H3, fill=COLORS["ink"])
        draw_text(draw, (x + 216, oy + 43), body, F_TINY, COLORS["muted"], width=500, max_lines=2)
        oy += 88
    rounded(draw, (x + 497, y + 438, x + 760, y + 478), 10, COLORS["blue"])
    draw.text((x + 578, y + 451), "保存并应用", font=F_SMALL, fill="#ffffff")
    card(draw, (x + 830, y + 72, x + w, y + 224), "API 字段", "Base URL\n模型名称\nAPI Key", COLORS["blue"], compact=True)
    card(draw, (x + 830, y + 250, x + w, y + 402), "本地说明", "本地模式会自动启动 models/Qwen/Qwen3-0.6B；首次加载可能需要几十秒。", COLORS["teal"], compact=True)
    return img


def frame_overview(idx: int) -> Image.Image:
    img, draw, area = page_frame("运维流程首页", "产品第一屏就是可执行的诊断飞行台。", idx, "运维流程")
    x, y, w, _ = area
    section_header(draw, area, "FAULT INJECTION · SRE RCA · EVOLUTION LOOP", "Ops Factory 诊断飞行台", "从故障数据进入，经过多模态证据、Agent 工具决策、Hermes RCA 和成效学习，形成可确认链路。")
    draw_pipeline(
        draw,
        y + 250,
        ["故障数据", "证据融合", "工具决策", "RCA 诊断", "恢复学习"],
        [COLORS["teal"], COLORS["blue"], COLORS["violet"], COLORS["rose"], COLORS["green"]],
        x1=x + 70,
        x2=x + 850,
        label_color=COLORS["muted"],
    )
    metric_tile(draw, (x, y + 386, x + 210, y + 470), "入口", "数据平台", COLORS["teal"])
    metric_tile(draw, (x + 232, y + 386, x + 442, y + 470), "辅助", "问诊台", COLORS["blue"])
    metric_tile(draw, (x + 464, y + 386, x + 674, y + 470), "核心", "根因定位", COLORS["rose"])
    metric_tile(draw, (x + 696, y + 386, x + 906, y + 470), "闭环", "成效看板", COLORS["green"])
    return img


def frame_datasource_entry(idx: int) -> Image.Image:
    img, draw, area = page_frame("数据平台总入口", "所有故障数据统一进入 RCA 工作流。", idx, "数据平台")
    x, y, w, _ = area
    section_header(draw, area, "STEP 1", "选择数据来源", "静态数据、动态 Kubernetes 注入、企业自定义数据并列。")
    card(draw, (x, y + 145, x + 292, y + 390), "Cloud-OpsBench", "静态故障快照数据集。适合离线演示、算法评估和可复现案例讲解。", COLORS["blue"])
    card(draw, (x + 314, y + 145, x + 606, y + 390), "实时故障注入", "Sock-Shop、Online-Shop、Train-Ticket，支持真实 Kubernetes 注入。", COLORS["rose"])
    card(draw, (x + 628, y + 145, x + 920, y + 390), "企业/自定义数据", "注册内部平台或自定义故障样本，统一进入证据和 RCA 面板。", COLORS["teal"])
    rounded(draw, (x, y + 420, x + w, y + 492), 14, "#f8fafc", COLORS["line"])
    draw.text((x + 24, y + 446), "工作流：选择数据源 -> 注入/注册故障 -> 多智能体诊断 -> 查看结果", font=F_H3, fill=COLORS["ink"])
    return img


def frame_static_case(idx: int) -> Image.Image:
    img, draw, area = page_frame("Cloud-OpsBench 静态案例", "搜索案例，选择后立即加载多模态证据。", idx, "数据平台")
    x, y, w, _ = area
    section_header(draw, area, "STATIC CASE", "离线案例选择", "适合产品介绍、课堂演示和算法对比评估。")
    rounded(draw, (x, y + 142, x + w, y + 190), 10, "#f8fafc", COLORS["line"])
    draw.text((x + 22, y + 157), "搜索案例：checkout latency / payment timeout / service error", font=F_BODY, fill=COLORS["muted"])
    rows = [
        ("boutique-042", "checkout 延迟上升，payment 下游超时", "选择"),
        ("train-ticket-118", "order-service 错误率增加，trace 出现长尾", "选择"),
        ("sock-shop-071", "catalogue CPU 飙升，前端 p95 变差", "选择"),
    ]
    ty = y + 214
    rounded(draw, (x, ty, x + w, ty + 198), 12, "#ffffff", COLORS["line"])
    for case_id, symptom, action in rows:
        draw.text((x + 24, ty + 18), case_id, font=F_H3, fill=COLORS["ink"])
        draw.text((x + 190, ty + 20), symptom, font=F_BODY, fill=COLORS["muted"])
        rounded(draw, (x + w - 96, ty + 14, x + w - 30, ty + 44), 8, COLORS["blue"])
        draw.text((x + w - 77, ty + 22), action, font=F_TINY, fill="#ffffff")
        draw.line((x + 18, ty + 62, x + w - 18, ty + 62), fill="#e2e8f0")
        ty += 64
    card(draw, (x, y + 436, x + w, y + 508), "选择后", "显示确认区、3D 拓扑、工具预案和 Log / Trace / Metric / Alert 原始数据。", COLORS["teal"], compact=True)
    return img


def frame_dynamic_injection(idx: int) -> Image.Image:
    img, draw, area = page_frame("动态 Kubernetes 故障注入", "面向真实演练和专业 SRE 验证。", idx, "数据平台")
    x, y, w, _ = area
    section_header(draw, area, "DYNAMIC FAULT", "实时故障注入配置", "选择平台、故障类型、目标服务和时间窗口，必须可访问真实 Kubernetes 集群。")
    labels = [
        ("平台", "Sock-Shop / Online-Shop / Train-Ticket"),
        ("故障类型", "pod_crash / high_cpu / latency / network"),
        ("目标服务", "payment / checkout / order / catalogue"),
        ("注入时间", "datetime-local"),
        ("持续秒数", "180"),
        ("观测窗口", "300"),
        ("采样间隔", "15"),
        ("执行模式", "live_kubernetes_required"),
    ]
    for i, (label, value) in enumerate(labels):
        col = i % 2
        row = i // 2
        bx = x + col * 462
        by = y + 150 + row * 76
        rounded(draw, (bx, by, bx + 430, by + 56), 10, "#ffffff", COLORS["line"])
        draw.text((bx + 18, by + 10), label, font=F_SMALL, fill=COLORS["muted"])
        draw.text((bx + 120, by + 18), value, font=F_BODY, fill=COLORS["ink"])
    rounded(draw, (x + 645, y + 466, x + 920, y + 512), 12, COLORS["rose"])
    draw.text((x + 724, y + 480), "注入真实故障", font=F_BODY, fill="#ffffff")
    return img


def frame_custom_data(idx: int) -> Image.Image:
    img, draw, area = page_frame("企业/自定义故障数据", "把内部平台数据接入统一 RCA 流程。", idx, "数据平台")
    x, y, w, _ = area
    section_header(draw, area, "ENTERPRISE DATA", "注册自定义故障案例", "支持 JSON 样本、根因服务、接口结构和企业数据源描述。")
    card(draw, (x, y + 145, x + 292, y + 322), "Case ID", "可选，不填自动生成。用于绑定后续问诊、RCA 和报告。", COLORS["blue"], compact=True)
    card(draw, (x + 314, y + 145, x + 606, y + 322), "根因服务", "例如 payment、checkout、order-service。用于 ACC@K / MRR 评估。", COLORS["rose"], compact=True)
    rounded(draw, (x + 628, y + 145, x + 920, y + 430), 12, "#0b1220")
    json_lines = [
        "{",
        '  "services": ["checkout", "payment", "cart"],',
        '  "metrics": [{"service": "payment", "p95": 1830}],',
        '  "logs": ["timeout calling payment gateway"],',
        '  "traces": [{"edge": "checkout -> payment"}],',
        '  "alerts": ["checkout SLO burn rate high"]',
        "}",
    ]
    ty = y + 168
    for line in json_lines:
        draw.text((x + 652, ty), line, font=F_MONO, fill="#dbeafe")
        ty += 28
    card(draw, (x, y + 346, x + 606, y + 430), "操作", "查看接口结构、填入示例、注册为数据源案例，然后进入根因分析。", COLORS["teal"], compact=True)
    return img


def frame_topology(idx: int) -> Image.Image:
    img, draw, area = page_frame("3D 系统总览与故障传播", "故障不是一个点，而是一条传播路径。", idx, "数据平台")
    x, y, w, _ = area
    section_header(draw, area, "TOPOLOGY", "服务拓扑与风险流", "根因服务、受影响服务、正常服务和依赖边用不同颜色表达。")
    canvas = (x, y + 140, x + 650, y + 500)
    rounded(draw, canvas, 16, "#0b1220", "#1e293b")
    nodes = [
        ("frontend", x + 90, y + 230, COLORS["green"]),
        ("checkout", x + 240, y + 318, COLORS["rose"]),
        ("payment", x + 420, y + 248, COLORS["rose"]),
        ("cart", x + 230, y + 438, COLORS["amber"]),
        ("inventory", x + 510, y + 405, COLORS["blue"]),
    ]
    edges = [(0, 1), (1, 2), (1, 3), (2, 4), (3, 4)]
    for a, b in edges:
        ax, ay = nodes[a][1], nodes[a][2]
        bx, by = nodes[b][1], nodes[b][2]
        draw.line((ax, ay, bx, by), fill="#67e8f9", width=3)
    for name, nx, ny, color in nodes:
        draw.ellipse((nx - 35, ny - 35, nx + 35, ny + 35), fill=color, outline="#ffffff", width=3)
        draw.text((nx - text_width(draw, name, F_TINY) / 2, ny + 45), name, font=F_TINY, fill="#dbeafe")
    card(draw, (x + 678, y + 160, x + w, y + 292), "图例", "红色：根因或强受影响服务\n琥珀：风险扩散节点\n绿色：健康节点\n蓝色：依赖服务", COLORS["rose"], compact=True)
    card(draw, (x + 678, y + 316, x + w, y + 472), "下一步", "拓扑旁边会同时展示多 Agent 工具调用预案和多模态原始证据，确保 RCA 前证据可核验。", COLORS["teal"], compact=True)
    return img


def frame_evidence(idx: int) -> Image.Image:
    img, draw, area = page_frame("原始多模态故障数据", "RCA 之前先看证据，而不是只看结论。", idx, "数据平台")
    x, y, w, _ = area
    section_header(draw, area, "EVIDENCE", "Log / Trace / Metric / Alert", "四类证据按标签页展示，进入 RCA 前可以人工确认数据质量。")
    tabs = [("Log", COLORS["blue"]), ("Trace", COLORS["violet"]), ("Metric", COLORS["teal"]), ("Alert", COLORS["rose"])]
    tx = x
    for label, color in tabs:
        rounded(draw, (tx, y + 146, tx + 110, y + 182), 10, color if label == "Log" else "#f8fafc", color)
        draw.text((tx + 35, y + 157), label, font=F_SMALL, fill="#ffffff" if label == "Log" else color)
        tx += 122
    rounded(draw, (x, y + 198, x + w, y + 506), 12, "#0b1220", "#1e293b")
    evidence = [
        "[log] checkout-service: payment gateway timeout after 1800ms",
        "[trace] frontend -> checkout -> payment span p95=2.4s",
        "[metric] payment cpu=91% error_rate=12.8% latency_p99=3.1s",
        "[alert] SLO burn rate high for checkout / payment dependency",
        "[topology] checkout depends on payment; cart and inventory are downstream victims",
    ]
    ty = y + 228
    for line in evidence:
        draw.text((x + 28, ty), line, font=F_MONO, fill="#dbeafe")
        ty += 44
    return img


def frame_tool_plan(idx: int) -> Image.Image:
    img, draw, area = page_frame("多 Agent 工具调用预案", "Human Confirm：执行前先说明为什么。", idx, "数据平台")
    x, y, w, _ = area
    section_header(draw, area, "TOOL PLAN", "工具路由透明化", "每个 Agent 说明输入、工具、预期产物和人工确认点。")
    agents = [
        ("SOP", "目标/停止条件", COLORS["teal"]),
        ("Context", "压缩证据", COLORS["blue"]),
        ("Memory", "检索经验", COLORS["violet"]),
        ("Router", "选择工具", COLORS["amber"]),
        ("Evidence", "执行工具", COLORS["cyan"]),
        ("Diagnosis", "Top-K 根因", COLORS["rose"]),
        ("Learner", "评估学习", COLORS["green"]),
    ]
    col_w = 128
    for i, (name, desc, color) in enumerate(agents):
        bx = x + i * (col_w + 4)
        rounded(draw, (bx, y + 160, bx + col_w, y + 420), 12, "#ffffff", COLORS["line"])
        rounded(draw, (bx + 16, y + 182, bx + 56, y + 222), 20, color)
        draw.text((bx + 19, y + 193), f"{i+1}", font=F_H3, fill="#ffffff")
        draw.text((bx + 16, y + 246), name, font=F_H3, fill=COLORS["ink"])
        draw_text(draw, (bx + 16, y + 282), desc, F_SMALL, COLORS["muted"], width=col_w - 32, max_lines=3)
        draw_text(draw, (bx + 16, y + 344), "状态：待确认\n产物：可审计", F_TINY, COLORS["muted"], width=col_w - 32, max_lines=2)
    rounded(draw, (x + 614, y + 448, x + w, y + 494), 12, COLORS["rose"])
    draw.text((x + 706, y + 462), "进入根因分析", font=F_BODY, fill="#ffffff")
    return img


def frame_consult(idx: int) -> Image.Image:
    img, draw, area = page_frame("运维问诊台", "自然语言问诊绑定当前 case 证据。", idx, "运维问诊台")
    x, y, w, _ = area
    section_header(draw, area, "OPS QUERY DESK", "非专家也能问清故障", "问题会绑定当前 case 的 log / trace / metric / topology 摘要。")
    card(draw, (x, y + 145, x + 420, y + 356), "问题输入", "这个故障更像根因服务异常，还是下游受害服务异常？请引用日志和拓扑证据。", COLORS["blue"])
    rounded(draw, (x + 450, y + 145, x + w, y + 428), 12, "#ffffff", COLORS["line"])
    draw.text((x + 478, y + 170), "证据回答", font=F_H2, fill=COLORS["ink"])
    draw_text(
        draw,
        (x + 478, y + 220),
        "更像 payment 是根因服务。证据包括 checkout -> payment 的 trace 长尾、payment 错误率升高、checkout 日志中的 gateway timeout，以及拓扑中 checkout 作为上游受害服务的传播方向。",
        F_BODY,
        COLORS["muted"],
        width=420,
        max_lines=6,
    )
    badge(draw, (x + 478, y + 374), "绑定 case: boutique-042", "#f0fdfa", "#99f6e4", COLORS["teal"])
    return img


def frame_rca_hub(idx: int) -> Image.Image:
    img, draw, area = page_frame("根因分析路径中心", "三条 RCA 路径服务不同团队成熟度。", idx, "根因分析")
    x, y, w, _ = area
    section_header(draw, area, "RCA HUB", "多智能体 / Hermes / 企业流程", "从数据平台绑定 case 后，用户可以选择最适合当前场景的诊断路径。")
    card(draw, (x, y + 145, x + 292, y + 418), "多智能体 RCA", "SOP、上下文、记忆、工具路由、证据、诊断、学习 Agent 逐步接力。", COLORS["blue"])
    card(draw, (x + 314, y + 145, x + 606, y + 418), "Hermes RCA Agent", "上下文胶囊、记忆检索、工具路由和失败学习，适合独立 Agent 路径演示。", COLORS["violet"])
    card(draw, (x + 628, y + 145, x + 920, y + 418), "企业 RCA 流程", "接入 graph_rca、runbook、internal_pipeline 或 MCP 工具。", COLORS["teal"])
    return img


def frame_multiagent(idx: int) -> Image.Image:
    img, draw, area = page_frame("多智能体 RCA 接力", "诊断过程被拆成可审计的 Agent 流程。", idx, "根因分析")
    x, y, w, _ = area
    section_header(draw, area, "GRAPH ORCHESTRATED", "SOP -> Context -> Memory -> Router -> Evidence -> Diagnosis -> Learner", "每一步都记录输入、输出、状态和下一步确认。")
    stages = [
        ("SOP", "success criteria"),
        ("Context", "evidence capsule"),
        ("Memory", "patterns"),
        ("Router", "tool shortlist"),
        ("Evidence", "tool outputs"),
        ("Diagnosis", "Top-K"),
        ("Learner", "metrics"),
    ]
    ty = y + 155
    for i, (name, desc) in enumerate(stages):
        bx = x + (i % 4) * 226
        by = ty + (i // 4) * 135
        color = [COLORS["teal"], COLORS["blue"], COLORS["violet"], COLORS["amber"], COLORS["cyan"], COLORS["rose"], COLORS["green"]][i]
        rounded(draw, (bx, by, bx + 206, by + 96), 12, "#ffffff", color, 2)
        badge(draw, (bx + 16, by + 14), f"Agent {i+1}", "#f8fafc", "#dbe3ef", COLORS["muted"])
        draw.text((bx + 16, by + 50), name, font=F_H3, fill=COLORS["ink"])
        draw.text((bx + 16, by + 74), desc, font=F_TINY, fill=COLORS["muted"])
    rounded(draw, (x, y + 445, x + w, y + 506), 12, "#f8fafc", COLORS["line"])
    draw.text((x + 24, y + 466), "Pipeline status: 等待确认下一步 -> 执行 -> 写入 live log -> 展示 Agent 输出 -> 进入最终结果", font=F_BODY, fill=COLORS["ink"])
    return img


def frame_agent_log(idx: int) -> Image.Image:
    img, draw, area = page_frame("Agent 实时日志与人工确认", "每一步都能看见为什么继续。", idx, "根因分析")
    x, y, w, _ = area
    section_header(draw, area, "LIVE AUDIT", "诊断执行链路可追溯", "按钮文案会随着当前 Agent 变化，支持继续和终止。")
    rounded(draw, (x, y + 150, x + 560, y + 500), 12, "#0b1220", "#1e293b")
    logs = [
        "SOP Agent: define objective=locate primary root cause",
        "Context Agent: compressed 118 logs, 42 spans, 13 metrics",
        "Memory Agent: retrieved 3 similar payment timeout cases",
        "Tool Router: choose trace_slice, metric_rank, log_cluster",
        "Evidence Agent: payment p95 and error rate dominate",
        "Diagnosis Agent: #1 payment score=0.91",
    ]
    ty = y + 176
    for line in logs:
        draw.text((x + 24, ty), line, font=F_MONO, fill="#dbeafe")
        ty += 46
    card(draw, (x + 590, y + 150, x + w, y + 284), "当前确认", "继续交给 Diagnosis Agent\n也可以终止本轮 RCA。", COLORS["rose"], compact=True)
    rounded(draw, (x + 590, y + 318, x + 758, y + 366), 12, COLORS["rose"])
    draw.text((x + 642, y + 334), "继续下一步", font=F_SMALL, fill="#ffffff")
    rounded(draw, (x + 778, y + 318, x + 918, y + 366), 12, "#f8fafc", COLORS["line"])
    draw.text((x + 824, y + 334), "终止", font=F_SMALL, fill=COLORS["ink"])
    card(draw, (x + 590, y + 398, x + w, y + 500), "输出", "LLM used、fallback、model、工具产物和候选根因会写入最终结果。", COLORS["teal"], compact=True)
    return img


def frame_hermes(idx: int) -> Image.Image:
    img, draw, area = page_frame("Hermes RCA Agent", "独立 Agent 路径适合展示上下文胶囊和工具路由。", idx, "根因分析")
    x, y, w, _ = area
    section_header(draw, area, "HERMES", "Context Capsule + Memory + Tools", "Hermes 路径和多智能体路径并列，但输出同样进入 RCA 结果和学习闭环。")
    cards = [
        ("上下文胶囊", "把故障、症状、证据、拓扑和目标压缩成结构化输入。", COLORS["blue"]),
        ("记忆检索", "检索相似成功经验和失败反例，避免重复踩坑。", COLORS["violet"]),
        ("工具路由", "选择 metric_rank、trace_slice、log_cluster 等工具。", COLORS["amber"]),
        ("失败学习", "未命中时生成原因、补丁和下一轮 replay 建议。", COLORS["green"]),
    ]
    for i, (title, body, color) in enumerate(cards):
        bx = x + (i % 2) * 462
        by = y + 150 + (i // 2) * 165
        card(draw, (bx, by, bx + 430, by + 138), title, body, color)
    return img


def frame_enterprise_rca(idx: int) -> Image.Image:
    img, draw, area = page_frame("企业 RCA 流程接入", "不替换企业已有能力，而是纳入统一入口。", idx, "根因分析")
    x, y, w, _ = area
    section_header(draw, area, "ENTERPRISE RCA", "注册内部算法 / Runbook / MCP 工具", "企业流程可以输出 Top-K 根因候选，并和平台内置 RCA 结果并列评估。")
    fields = [
        ("流程名称", "Graph RCA Pipeline"),
        ("Endpoint / 调用标识", "mcp://internal-rca/runbook"),
        ("类型", "graph_rca / runbook / internal_pipeline"),
        ("输出约定", "Top-K candidates + evidence + confidence"),
    ]
    for i, (label, value) in enumerate(fields):
        bx = x + (i % 2) * 462
        by = y + 145 + (i // 2) * 86
        rounded(draw, (bx, by, bx + 430, by + 58), 10, "#ffffff", COLORS["line"])
        draw.text((bx + 18, by + 9), label, font=F_SMALL, fill=COLORS["muted"])
        draw.text((bx + 18, by + 32), value, font=F_BODY, fill=COLORS["ink"])
    card(draw, (x, y + 340, x + 442, y + 496), "已接入流程", "graph_rca · internal-pipeline\nrunbook · checkout-incident\nmcp_tool · evidence-search", COLORS["teal"], compact=True)
    card(draw, (x + 478, y + 340, x + w, y + 496), "评估方式", "选择此流程后重新评估当前 case，结果进入同一套 ACC@K / MRR / 报告体系。", COLORS["blue"], compact=True)
    return img


def frame_results(idx: int) -> Image.Image:
    img, draw, area = page_frame("RCA 结果面板", "结果既看结论，也看模型、工具和评估指标。", idx, "根因分析")
    x, y, w, _ = area
    section_header(draw, area, "RCA RESULT", "Top-K 根因候选与评估", "展示 LLM 调用、候选根因、Ground Truth、ACC@K、MRR 和模型输入摘要。")
    rounded(draw, (x, y + 145, x + 400, y + 500), 12, "#ffffff", COLORS["line"])
    draw.text((x + 24, y + 170), "候选根因", font=F_H2, fill=COLORS["ink"])
    candidates = [("#1", "payment", "0.912"), ("#2", "checkout", "0.584"), ("#3", "gateway", "0.411"), ("#4", "cart", "0.279"), ("#5", "inventory", "0.154")]
    ty = y + 220
    for rank, svc, score in candidates:
        rounded(draw, (x + 24, ty, x + 370, ty + 42), 9, "#f8fafc", "#e2e8f0")
        draw.text((x + 42, ty + 11), rank, font=F_SMALL, fill=COLORS["rose"] if rank == "#1" else COLORS["muted"])
        draw.text((x + 95, ty + 9), svc, font=F_BODY, fill=COLORS["ink"])
        draw.text((x + 282, ty + 11), score, font=F_SMALL, fill=COLORS["muted"])
        ty += 52
    metric_tile(draw, (x + 430, y + 145, x + 644, y + 228), "LLM 调用", "已使用", COLORS["teal"])
    metric_tile(draw, (x + 666, y + 145, x + 880, y + 228), "模型", "Qwen-0.6B", COLORS["blue"])
    metric_tile(draw, (x + 430, y + 252, x + 644, y + 335), "ACC@1", "Hit", COLORS["green"])
    metric_tile(draw, (x + 666, y + 252, x + 880, y + 335), "MRR", "1.000", COLORS["amber"])
    card(draw, (x + 430, y + 360, x + w, y + 500), "模型输入摘要", "症状、拓扑、指标、日志聚类、trace 长尾、工具计划和证据计数都会进入可查看摘要。", COLORS["violet"], compact=True)
    return img


def frame_report_restore(idx: int) -> Image.Image:
    img, draw, area = page_frame("报告生成与故障恢复校验", "交付闭环不能止步于结论。", idx, "根因分析")
    x, y, w, _ = area
    section_header(draw, area, "REPORT & RECOVERY", "PDF 报告 + Kubernetes 回查", "恢复成功必须满足真实集群动作和状态回查，不只是前端状态变化。")
    card(draw, (x, y + 145, x + 292, y + 410), "PDF 诊断报告", "包含故障上下文、Agent 执行链路、Top-K 根因、评估指标、证据摘要和恢复建议。", COLORS["blue"])
    card(draw, (x + 314, y + 145, x + 606, y + 410), "真实恢复", "pod_crash / scale 恢复副本；CPU、内存、延迟、网络等清理注入副作用并重启。", COLORS["green"])
    card(draw, (x + 628, y + 145, x + 920, y + 410), "验收条件", "actual_cluster_recovery=true 且 restore_verified=true 才显示恢复成功。", COLORS["rose"])
    rounded(draw, (x, y + 440, x + w, y + 498), 12, "#ecfdf5", "#86efac")
    draw.text((x + 24, y + 460), "回查：desired=3 ready=3 available=3 updated=3 unavailable=0 observed_generation=ok", font=F_BODY, fill="#166534")
    return img


def frame_chat(idx: int) -> Image.Image:
    img, draw, area = page_frame("模型交互与诊断复盘", "把 RCA 结果转成可问、可解释、可导出的知识。", idx, "模型交互")
    x, y, w, _ = area
    section_header(draw, area, "MODEL CONSOLE", "复盘、质疑、解释、生成排查计划", "模型工作台使用同一套模型来源策略：默认本地 Qwen-0.6B，用户可主动切换自己的 API。")
    card(draw, (x, y + 145, x + 270, y + 420), "模型状态", "本地 Qwen-0.6B\n选择模型来源\n清空 / 导出", COLORS["teal"], compact=True)
    quick = [
        "复盘最近 RCA",
        "分析自进化",
        "生成排查计划",
        "解释拓扑影响",
    ]
    qy = y + 160
    for item in quick:
        rounded(draw, (x + 304, qy, x + 540, qy + 46), 10, "#f8fafc", COLORS["line"])
        draw.text((x + 326, qy + 15), item, font=F_SMALL, fill=COLORS["ink"])
        qy += 60
    rounded(draw, (x + 574, y + 145, x + w, y + 420), 12, "#ffffff", COLORS["line"])
    draw.text((x + 602, y + 174), "对话复盘", font=F_H2, fill=COLORS["ink"])
    draw_text(draw, (x + 602, y + 220), "请解释上一轮 RCA 为什么 Top1 命中，并说明下一轮如果未命中应该优先补充哪类证据。", F_BODY, COLORS["muted"], width=300)
    rounded(draw, (x + 602, y + 340, x + w - 28, y + 386), 12, COLORS["blue"])
    draw.text((x + 772, y + 354), "Send", font=F_BODY, fill="#ffffff")
    return img


def frame_chat_sessions(idx: int) -> Image.Image:
    img, draw, area = page_frame("会话与上下文注入", "交互不是孤立聊天，而是围绕诊断资产工作。", idx, "模型交互")
    x, y, w, _ = area
    section_header(draw, area, "HUMAN-IN-THE-LOOP", "上下文快捷动作与会话管理", "适合售前演示、交付复盘、专家追问和团队培训。")
    card(draw, (x, y + 145, x + 292, y + 390), "会话历史", "刷新会话、查看历史、清空或导出，便于把一次诊断变成复盘材料。", COLORS["blue"])
    card(draw, (x + 314, y + 145, x + 606, y + 390), "上下文注入", "快捷动作自动围绕最近 RCA、拓扑影响、自进化记录和 K8s 排查计划组织问题。", COLORS["violet"])
    card(draw, (x + 628, y + 145, x + 920, y + 390), "边界一致", "模型来源显示在全局顶部和模型工作台，默认仍是本地 Qwen-0.6B。", COLORS["teal"])
    return img


def frame_guard_target(idx: int) -> Image.Image:
    img, draw, area = page_frame("持续守护对象接入", "把重复巡检做成可确认计划。", idx, "持续守护")
    x, y, w, _ = area
    section_header(draw, area, "CONTINUOUS RELIABILITY GUARD", "真实系统端口或内置模拟系统", "支持 Host / URL、Port、Health Path、系统沙盘 Path，也支持一键内置可观测模拟。")
    card(draw, (x, y + 145, x + 292, y + 390), "真实系统端口", "Host / URL\nPort\nHealth Path\nToken / Header", COLORS["blue"], compact=True)
    card(draw, (x + 314, y + 145, x + 606, y + 390), "内置可观测模拟", "没有真实系统时，也能演示巡检、风险传播和 RCA 预热。", COLORS["teal"])
    rounded(draw, (x + 628, y + 145, x + 920, y + 390), 12, "#0b1220", "#1e293b")
    draw.text((x + 658, y + 178), "智能巡检沙盘", font=F_H2, fill="#ffffff")
    for i, label in enumerate(["checkout", "payment", "catalog", "cart"]):
        nx = x + 700 + (i % 2) * 110
        ny = y + 248 + (i // 2) * 72
        draw.ellipse((nx - 25, ny - 25, nx + 25, ny + 25), fill=[COLORS["green"], COLORS["amber"], COLORS["blue"], COLORS["teal"]][i])
        draw.text((nx - text_width(draw, label, F_TINY) / 2, ny + 32), label, font=F_TINY, fill="#dbeafe")
    return img


def frame_guard_scenarios(idx: int) -> Image.Image:
    img, draw, area = page_frame("持续守护 15 类模拟场景", "覆盖常见电商与微服务运行风险。", idx, "持续守护")
    x, y, w, _ = area
    section_header(draw, area, "SCENARIO LIBRARY", "内置可观测模拟系统", "在没有真实系统时，也能展示风险传播、RCA 预热、报告和人工确认门。")
    scenarios = [
        "checkout_latency", "catalog_error", "node_pressure", "payment_timeout", "inventory_db_lock",
        "recommendation_storm", "gateway_traffic_spike", "shipping_queue_backlog", "user_auth_error",
        "cart_cache_hotspot", "database_disk_io", "service_mesh_config_drift", "retry_storm",
        "deployment_version_skew", "observability_gap",
    ]
    for i, item in enumerate(scenarios):
        col = i % 3
        row = i // 3
        bx = x + col * 306
        by = y + 146 + row * 58
        color = [COLORS["blue"], COLORS["teal"], COLORS["amber"], COLORS["rose"], COLORS["violet"]][i % 5]
        rounded(draw, (bx, by, bx + 286, by + 42), 10, "#ffffff", color)
        draw.text((bx + 16, by + 13), item, font=F_TINY, fill=COLORS["ink"])
    return img


def frame_guard_plan(idx: int) -> Image.Image:
    img, draw, area = page_frame("守护计划与报告", "巡检可自动，风险动作仍由人确认。", idx, "持续守护")
    x, y, w, _ = area
    section_header(draw, area, "PLAN & BOUNDARY", "目标、范围、频率、风险等级", "系统职责是持续观察、证据聚合、RCA 预热和报告沉淀；高风险动作需要人工确认。")
    card(draw, (x, y + 145, x + 292, y + 390), "计划配置", "目标：检查 SLO 和级联风险\n频率：手动 / 每小时 / 每日 / 告警触发\n风险：低 / 中 / 高 / 关键", COLORS["blue"], compact=True)
    card(draw, (x + 314, y + 145, x + 606, y + 390), "执行体边界", "默认只读；恢复、扩缩容、注入等动作必须人工确认。", COLORS["rose"])
    card(draw, (x + 628, y + 145, x + 920, y + 390), "报告沉淀", "计划结果写入守护报告，可供 RCA、复盘和交付材料复用。", COLORS["teal"])
    return img


def frame_collection_config(idx: int) -> Image.Image:
    img, draw, area = page_frame("故障数据收集", "把动态故障变成训练、偏好和评估数据。", idx, "故障数据收集")
    x, y, w, _ = area
    section_header(draw, area, "DATA COLLECTION", "SFT / RL / Eval / Custom", "从 Sock-Shop、Online-Shop、Train-Ticket 持续注入故障并整理成训练样本。")
    card(draw, (x, y + 145, x + 292, y + 390), "平台", "Sock-Shop\nOnline-Shop\nTrain-Ticket", COLORS["blue"], compact=True)
    card(draw, (x + 314, y + 145, x + 606, y + 390), "训练格式", "SFT / Alpaca\nRL / Preference\n用户自定义模板", COLORS["violet"], compact=True)
    card(draw, (x + 628, y + 145, x + 920, y + 390), "采样配置", "每个平台轮数、故障持续秒数、观测窗口和采样间隔。", COLORS["teal"])
    rounded(draw, (x, y + 420, x + w, y + 496), 12, "#f8fafc", COLORS["line"])
    draw.text((x + 24, y + 446), "一键启动持续采集 -> 会话历史 -> 结果预览 -> 数据目录沉淀", font=F_H3, fill=COLORS["ink"])
    return img


def frame_collection_preview(idx: int) -> Image.Image:
    img, draw, area = page_frame("采集结果预览", "让数据真的能被训练、评估和复盘使用。", idx, "故障数据收集")
    x, y, w, _ = area
    section_header(draw, area, "SESSION PREVIEW", "任务摘要与 JSON 预览", "采集结果展示平台、轮次、样本数量、错误信息和格式化样本。")
    metric_tile(draw, (x, y + 145, x + 214, y + 228), "Format", "alpaca_sft", COLORS["blue"])
    metric_tile(draw, (x + 235, y + 145, x + 449, y + 228), "Platforms", "3", COLORS["teal"])
    metric_tile(draw, (x + 470, y + 145, x + 684, y + 228), "Samples", "128", COLORS["green"])
    metric_tile(draw, (x + 705, y + 145, x + 920, y + 228), "Errors", "0", COLORS["amber"])
    rounded(draw, (x, y + 258, x + w, y + 508), 12, "#0b1220", "#1e293b")
    sample = [
        "{",
        '  "instruction": "Locate root cause from logs, traces, metrics and topology",',
        '  "input": "checkout latency, payment timeout, trace p95 high",',
        '  "output": "payment is the primary root cause; checkout is affected",',
        '  "metadata": {"platform": "online-shopping", "fault": "payment_timeout"}',
        "}",
    ]
    ty = y + 286
    for line in sample:
        draw.text((x + 28, ty), line, font=F_MONO, fill="#dbeafe")
        ty += 34
    return img


def frame_evolution_dashboard(idx: int) -> Image.Image:
    img, draw, area = page_frame("成效看板", "判断系统是否真的在变好。", idx, "成效看板")
    x, y, w, _ = area
    section_header(draw, area, "EVALUATION", "成功率、MRR、LLM 使用率和趋势", "RCA 完成后，成效数据进入看板，用于观察工具策略和 Agent 能力是否持续提升。")
    metric_tile(draw, (x, y + 145, x + 214, y + 228), "累计运行", "42", COLORS["blue"])
    metric_tile(draw, (x + 235, y + 145, x + 449, y + 228), "成功率", "78.6%", COLORS["green"])
    metric_tile(draw, (x + 470, y + 145, x + 684, y + 228), "平均 MRR", "0.73", COLORS["amber"])
    metric_tile(draw, (x + 705, y + 145, x + 920, y + 228), "LLM 使用率", "100%", COLORS["teal"])
    rounded(draw, (x, y + 258, x + 448, y + 504), 12, "#ffffff", COLORS["line"])
    draw.text((x + 24, y + 282), "成功率趋势", font=F_H3, fill=COLORS["ink"])
    pts = [(x + 42, y + 450), (x + 115, y + 422), (x + 188, y + 432), (x + 261, y + 360), (x + 334, y + 342), (x + 406, y + 300)]
    draw.line(pts, fill=COLORS["blue"], width=4)
    for px, py in pts:
        draw.ellipse((px - 5, py - 5, px + 5, py + 5), fill=COLORS["blue"])
    card(draw, (x + 480, y + 258, x + w, y + 504), "成功与失败总结", "按数据源、平台、故障类型、工具链和模型路径聚合，保留成功经验和失败 case。", COLORS["violet"])
    return img


def frame_failure_learning(idx: int) -> Image.Image:
    img, draw, area = page_frame("失败学习与 Harness vNext", "让一次未命中变成下一轮更强的系统。", idx, "成效看板")
    x, y, w, _ = area
    section_header(draw, area, "SELF EVOLUTION", "失败 case -> 归因 -> 补丁 -> Replay -> 发布", "失败样本会生成改进候选，离线 Replay 通过后才能发布 Harness vNext。")
    steps = [
        ("失败 case", "Top1 未命中 checkout/payment 传播方向"),
        ("归因", "metric_rank 权重不足，trace 长尾未参与"),
        ("补丁", "Prompt + tool routing policy patch"),
        ("Replay", "离线回放守门，检查回归风险"),
        ("vNext", "发布新的 Harness 策略"),
    ]
    for i, (title, body) in enumerate(steps):
        bx = x + i * 184
        rounded(draw, (bx, y + 172, bx + 164, y + 390), 12, "#ffffff", COLORS["line"])
        rounded(draw, (bx + 22, y + 196, bx + 62, y + 236), 20, [COLORS["rose"], COLORS["amber"], COLORS["blue"], COLORS["teal"], COLORS["green"]][i])
        draw.text((bx + 35, y + 207), str(i + 1), font=F_SMALL, fill="#ffffff")
        draw.text((bx + 20, y + 260), title, font=F_H3, fill=COLORS["ink"])
        draw_text(draw, (bx + 20, y + 292), body, F_SMALL, COLORS["muted"], width=124, max_lines=4)
    rounded(draw, (x + 618, y + 430, x + w, y + 498), 12, COLORS["green"])
    draw.text((x + 710, y + 454), "发布 Harness vNext", font=F_BODY, fill="#ffffff")
    return img


def frame_final(idx: int) -> Image.Image:
    img = dark_gradient()
    draw = ImageDraw.Draw(img)
    badge(draw, (62, 52), "DELIVERY READY", "#102a43", "#1e6f8f", "#c7f9ff")
    draw.text((62, 118), "全功能演示已覆盖", font=F_HERO, fill="#ffffff")
    draw_text(draw, (65, 184), "一键安装、一键启动、本地 Qwen-0.6B 默认模型、用户自带 API、8 个主导航、3 条 RCA 路径、持续守护、数据收集和成效学习全部进入 README 交付材料。", F_H2, "#d7e4ff", width=840, max_lines=3)
    checklist = [
        "默认本地 Qwen-0.6B，无内置外部 API",
        "用户可主动接入自己的 OpenAI-compatible / Anthropic-compatible API",
        "零基础机器自动安装 Python、依赖、模型和工具",
        "完整 Demo GIF + Storyboard + Feature Map",
        "README 可作为产品介绍会材料直接展示",
    ]
    y = 328
    for item in checklist:
        rounded(draw, (86, y, 1110, y + 52), 14, "#ffffff", "#a5f3fc")
        rounded(draw, (108, y + 14, 132, y + 38), 12, COLORS["green"])
        draw.text((114, y + 17), "✓", font=F_SMALL, fill="#ffffff")
        draw.text((156, y + 16), item, font=F_H3, fill=COLORS["ink"])
        y += 68
    return img


def build_frames() -> list[Image.Image]:
    builders = [
        frame_cover,
        frame_install,
        frame_launch,
        frame_model_boundary,
        frame_model_modal,
        frame_overview,
        frame_datasource_entry,
        frame_static_case,
        frame_dynamic_injection,
        frame_custom_data,
        frame_topology,
        frame_evidence,
        frame_tool_plan,
        frame_consult,
        frame_rca_hub,
        frame_multiagent,
        frame_agent_log,
        frame_hermes,
        frame_enterprise_rca,
        frame_results,
        frame_report_restore,
        frame_chat,
        frame_chat_sessions,
        frame_guard_target,
        frame_guard_scenarios,
        frame_guard_plan,
        frame_collection_config,
        frame_collection_preview,
        frame_evolution_dashboard,
        frame_failure_learning,
        frame_final,
    ]
    return [builder(i + 1) for i, builder in enumerate(builders)]


def write_storyboard() -> None:
    lines = [
        "# Ops Factory Full Demo Storyboard",
        "",
        "这个逐镜头讲解稿对应 README 中嵌入的 `opsfactory-demo.gif`。它覆盖主界面、部署、模型治理、数据平台、问诊、RCA、恢复、持续守护、数据收集和成效学习，适合产品介绍会、售前演示、交付验收和培训讲解。",
        "",
        "## 讲解节奏",
        "",
        "- 建议播放时长：约 60 秒。",
        "- 建议讲解方式：先讲价值闭环，再按左侧主导航逐项展开。",
        "- 关键边界：Ops Factory 不内置外部 API；默认本地 Qwen-0.6B；用户只有主动选择并填写自己的 API 时才切换。",
        "",
        "## 逐镜头脚本",
        "",
    ]
    for idx, scene in enumerate(SCENES, 1):
        lines.extend(
            [
                f"### {idx:02}. {scene.title}",
                "",
                f"- 展示重点：{scene.focus}",
                f"- 讲解词：{scene.talk_track}",
                "",
            ]
        )
    lines.extend(
        [
            "## 演示验收口径",
            "",
            "- 覆盖 8 个主导航：运维流程、数据平台、运维问诊台、根因分析、持续守护、故障数据收集、模型交互、成效看板。",
            "- 覆盖 3 类数据来源：Cloud-OpsBench 静态案例、动态 Kubernetes 故障注入、企业/自定义数据。",
            "- 覆盖 3 条 RCA 路径：多智能体 RCA、Hermes RCA Agent、企业 RCA 流程。",
            "- 覆盖模型选择：本地 Qwen-0.6B、用户自带 OpenAI-compatible API、用户自带 Anthropic-compatible API。",
            "- 覆盖持续守护 15 类模拟场景。",
            "- 覆盖训练数据收集、成效看板、失败学习和 Harness vNext 发布闭环。",
            "",
        ]
    )
    STORYBOARD_PATH.write_text("\n".join(lines), encoding="utf-8")


def write_feature_map() -> None:
    rows = [
        ("一键安装", "setup_opsfactory_env.sh", "自动安装私有 Python、.venv、依赖、本地模型、kubectl/kind、env 文件。", "镜头 02"),
        ("一键启动", "start_opsfactory.sh", "支持前台、后台、tmux、restart、stop、端口占用处理和日志路径。", "镜头 03"),
        ("模型默认策略", "全局模型入口", "默认本地 Qwen-0.6B，不内置外部 API。", "镜头 04"),
        ("用户自带 API", "模型来源弹窗", "支持 OpenAI-compatible 与 Anthropic-compatible，用户主动填写自己的参数。", "镜头 05"),
        ("运维流程", "首页", "诊断飞行台、闭环链路、核心入口。", "镜头 06"),
        ("数据平台入口", "数据来源选择", "静态、动态、企业自定义三类入口。", "镜头 07"),
        ("静态案例", "Cloud-OpsBench", "案例搜索、选择、确认进入 RCA。", "镜头 08"),
        ("动态注入", "Kubernetes", "平台、故障、目标、时间、持续、窗口、采样、真实注入。", "镜头 09"),
        ("企业数据", "Custom JSON", "Case ID、根因服务、接口结构、示例和注册。", "镜头 10"),
        ("3D 拓扑", "故障传播", "根因服务、受影响服务、正常服务、依赖边和风险流。", "镜头 11"),
        ("原始证据", "Log/Trace/Metric/Alert", "四类证据标签页、证据核验。", "镜头 12"),
        ("工具预案", "Human Confirm", "工具调用理由、输入、预期产物和人工确认。", "镜头 13"),
        ("运维问诊台", "Ops Query Desk", "自然语言问诊，答案绑定当前 case 证据。", "镜头 14"),
        ("RCA 路径中心", "RCA Hub", "多智能体、Hermes、企业 RCA 流程并列选择。", "镜头 15"),
        ("多智能体 RCA", "Graph Orchestrated", "SOP、上下文、记忆、工具路由、证据、诊断、学习。", "镜头 16-17"),
        ("Hermes RCA", "Hermes Agent", "上下文胶囊、记忆检索、工具路由、失败学习。", "镜头 18"),
        ("企业 RCA", "Enterprise Flow", "内部算法、Runbook、graph_rca、MCP 工具注册。", "镜头 19"),
        ("RCA 结果", "Result Panel", "Top-K、LLM 状态、ACC@K、MRR、Ground Truth、输入摘要。", "镜头 20"),
        ("报告恢复", "PDF + Recovery", "PDF 诊断报告、真实恢复、K8s 回查和验收条件。", "镜头 21"),
        ("模型交互", "Model Console", "RCA 复盘、排查计划、拓扑解释、导出、会话管理。", "镜头 22-23"),
        ("持续守护", "Guard", "真实系统端口、内置模拟系统、智能巡检沙盘。", "镜头 24"),
        ("守护场景", "Scenario Library", "15 类模拟风险场景。", "镜头 25"),
        ("守护计划", "Plan & Report", "目标、范围、频率、风险等级、只读边界和人工确认。", "镜头 26"),
        ("故障数据收集", "Collection", "SFT、RL、Eval、自定义模板、平台轮次和采样窗口。", "镜头 27-28"),
        ("成效看板", "Evolution", "成功率、MRR、LLM 使用率、趋势、成功与失败总结。", "镜头 29"),
        ("失败学习", "Harness vNext", "失败归因、补丁、离线 Replay、发布门禁。", "镜头 30"),
        ("交付验收", "README 资产", "GIF、海报、逐镜头脚本、功能覆盖图。", "镜头 31"),
    ]
    lines = [
        "# Ops Factory Demo Feature Map",
        "",
        "本文件记录 README 演示 GIF 覆盖的功能点，便于交付验收、售前讲解和二次录制时逐项核对。",
        "",
        "| 功能域 | 入口/模块 | 展示内容 | Demo 镜头 |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(f"| {domain} | {entry} | {content} | {scene} |" for domain, entry, content, scene in rows)
    lines.append("")
    FEATURE_MAP_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frames = build_frames()
    frames[0].save(POSTER_PATH)
    frames[0].save(
        GIF_PATH,
        save_all=True,
        append_images=frames[1:],
        duration=[2600] + [1850] * (len(frames) - 2) + [2800],
        loop=0,
        optimize=True,
    )
    write_storyboard()
    write_feature_map()
    print(f"wrote {GIF_PATH}")
    print(f"wrote {POSTER_PATH}")
    print(f"wrote {STORYBOARD_PATH}")
    print(f"wrote {FEATURE_MAP_PATH}")


if __name__ == "__main__":
    main()
