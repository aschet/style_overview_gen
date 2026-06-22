# Copyright 2026 Thomas Ascher <thomas.ascher@gmx.at>
# SPDX-License-Identifier: MIT

import argparse
import json
import math
import os
import re
import sys
import uuid
import websocket
import urllib.request
import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

SERVER = "127.0.0.1:8188"
CLIENT_ID = str(uuid.uuid4())
FIXED_SEED = 754254705340088
PROMPT_TITLE = "Prompt"
SEED_TITLE = "Seed"
DEFAULT_TIMEOUT = 600.0
STAMP_PNG_KEY = "StyleOverviewStamp"


def load_prompts(path: Path) -> tuple[list[dict], str, str]:
    """Load prompts from a Markdown file.
    
    Format:
    # PREFIX
    Common prefix text added to all prompts (optional)
    
    # Title
    Prompt text here...
    
    # Another Title
    Another prompt text...
    
    # POSTFIX
    Common postfix text added to all prompts (optional)
    """
    with path.open("r", encoding="utf-8") as handle:
        content = handle.read()
    
    prompts: list[dict] = []
    prefix_text = ""
    postfix_text = ""
    
    # Split by lines starting with #
    lines = content.split('\n')
    current_title = None
    current_prompt = []
    
    for line in lines:
        if line.startswith('# '):
            # Save previous prompt if exists
            if current_title is not None:
                prompt_text = '\n'.join(current_prompt).strip()
                if prompt_text:
                    if current_title.upper() == "PREFIX":
                        prefix_text = prompt_text
                    elif current_title.upper() == "POSTFIX":
                        postfix_text = prompt_text
                    else:
                        prompts.append({"title": current_title, "prompt": prompt_text})
            # Start new prompt
            current_title = line[2:].strip()  # Remove '# ' and strip whitespace
            current_prompt = []
        else:
            if current_title is not None:
                current_prompt.append(line)
    
    # Save last prompt
    if current_title is not None:
        prompt_text = '\n'.join(current_prompt).strip()
        if prompt_text:
            if current_title.upper() == "PREFIX":
                prefix_text = prompt_text
            elif current_title.upper() == "POSTFIX":
                postfix_text = prompt_text
            else:
                prompts.append({"title": current_title, "prompt": prompt_text})
    
    if not prompts:
        raise ValueError(f"No prompts found in {path}")
    
    # Apply prefix and postfix to all prompts
    for prompt in prompts:
        full_prompt = prompt["prompt"]
        if prefix_text:
            full_prompt = prefix_text + "\n\n" + full_prompt
        if postfix_text:
            full_prompt = full_prompt + "\n\n" + postfix_text
        prompt["prompt"] = full_prompt
    
    return prompts, prefix_text, postfix_text


def write_expanded_prompts_file(path: Path, prompts: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    expanded_path = output_dir / path.name
    lines: list[str] = []
    for prompt in prompts:
        title = prompt.get("title")
        prompt_text = prompt.get("prompt", "").strip()
        if title is None or not prompt_text:
            continue
        lines.append(f"# {title}")
        lines.append(prompt_text)
        lines.append("")
    expanded_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def load_workflow(path: Path) -> dict:
    """Load a workflow JSON file."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_prompt_and_seed_nodes(workflow: dict) -> tuple[list[dict], list[dict]]:
    """Identify workflow nodes for prompt and seed by title."""
    prompt_nodes: list[dict] = []
    seed_nodes: list[dict] = []
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        meta = node.get("_meta", {})
        if node.get("class_type") == "PrimitiveStringMultiline" and meta.get("title") == PROMPT_TITLE:
            prompt_nodes.append(node)
        elif node.get("class_type") == "PrimitiveInt" and meta.get("title") == SEED_TITLE:
            seed_nodes.append(node)
    return prompt_nodes, seed_nodes


def apply_prompt_to_workflow(workflow: dict, prompt: str, seed: int = FIXED_SEED) -> dict:
    """Return a new workflow object with prompt and seed values updated."""
    cloned = json.loads(json.dumps(workflow))
    prompt_nodes, seed_nodes = find_prompt_and_seed_nodes(cloned)

    if not prompt_nodes:
        raise ValueError("No prompt node found in workflow")
    if not seed_nodes:
        raise ValueError("No seed node found in workflow")

    for node in prompt_nodes:
        node_inputs = node.setdefault("inputs", {})
        node_inputs["value"] = prompt

    for node in seed_nodes:
        node_inputs = node.setdefault("inputs", {})
        node_inputs["value"] = seed

    return cloned


def load_font(size: int) -> ImageFont.ImageFont:
    font_names = ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf"]
    if os.name == "nt":
        font_names = ["arialbd.ttf", "arial.ttf"] + font_names

    for name in font_names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def measure_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    if hasattr(draw, "textbbox"):
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        return right - left, bottom - top
    return draw.textsize(text, font=font)


def create_collage(image_items: list[tuple[bytes, str]], title_text: str, output_path: Path) -> None:
    if not image_items:
        raise ValueError("No images available to build collage")

    tile_size = 512
    max_columns = 4
    label_font = load_font(18)
    label_padding = 8
    separator_width = 3

    tiles: list[Image.Image] = []
    labels: list[str] = []
    for raw_data, label in image_items:
        with Image.open(BytesIO(raw_data)) as raw_image:
            tile = raw_image.convert("RGB")
            tile = tile.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
            tiles.append(tile)
            labels.append(label)

    columns = min(max_columns, len(tiles))
    rows = math.ceil(len(tiles) / columns)

    title_font = load_font(32)
    # Measure text sizes using a temporary draw
    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    title_width, title_height = measure_text(dummy_draw, title_text, title_font)

    label_heights = [measure_text(dummy_draw, label, label_font)[1] for label in labels]
    label_height = max(label_heights) + label_padding * 2 if labels else 0
    title_area = title_height + 32

    collage_width = columns * tile_size
    collage_height = title_area + rows * (tile_size + label_height)
    collage = Image.new("RGB", (collage_width, collage_height), "black")
    draw = ImageDraw.Draw(collage)

    title_x = int((collage_width - title_width) / 2)
    title_y = 16
    draw.text((title_x, title_y), title_text, fill="white", font=title_font)

    tile_area_top = title_area
    tile_area_bottom = title_area + rows * (tile_size + label_height) - label_height

    for index, tile in enumerate(tiles):
        row = index // columns
        col = index % columns
        x = col * tile_size
        y = tile_area_top + row * (tile_size + label_height)
        collage.paste(tile, (x, y))

        label_text = labels[index]
        label_width, _ = measure_text(draw, label_text, label_font)
        label_x = x + (tile_size - label_width) / 2
        label_y = y + tile_size + label_padding
        draw.text((label_x, label_y), label_text, fill="white", font=label_font)

    # Draw black vertical separators between tile columns
    if columns > 1:
        for col in range(1, columns):
            x_line = col * tile_size
            # draw a filled rectangle to ensure consistent width
            half = separator_width // 2
            draw.rectangle((x_line - half, tile_area_top, x_line + half, tile_area_bottom), fill="black")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    collage.save(output_path, format="JPEG", quality=90, optimize=True, progressive=True)


def normalize_timeout(timeout: float) -> float | None:
    if timeout <= 0:
        return None
    return timeout


def connect(timeout: float = DEFAULT_TIMEOUT) -> websocket.WebSocket:
    ws = websocket.WebSocket()
    normalized = normalize_timeout(timeout)
    ws.settimeout(normalized)
    try:
        ws.connect(f"ws://{SERVER}/ws?clientId={CLIENT_ID}", timeout=normalized)
    except OSError as exc:
        raise ConnectionError(
            f"Connection failed: unable to connect to ws://{SERVER}/ws. "
            f"Check that the server is running and reachable, and verify the --server address. "
            f"Original error: {exc}"
        ) from exc
    return ws


def wait_for_completion(ws: websocket.WebSocket, prompt_id: str) -> None:
    while True:
        msg = ws.recv()
        if isinstance(msg, str):
            data = json.loads(msg)
            if data.get("type") == "executing":
                d = data.get("data", {})
                if d.get("node") is None and d.get("prompt_id") == prompt_id:
                    return
            elif data.get("type") == "execution_error":
                raise RuntimeError(data.get("data"))


def queue_prompt(workflow: dict, timeout: float = DEFAULT_TIMEOUT) -> str:
    normalized = normalize_timeout(timeout)
    body = json.dumps({"prompt": workflow, "client_id": CLIENT_ID}).encode("utf-8")
    request = urllib.request.Request(
        f"http://{SERVER}/prompt",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=normalized) as resp:
        response_text = resp.read().decode()

    if not response_text.strip():
        raise RuntimeError("Empty response from server /prompt endpoint")

    try:
        response = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to decode /prompt response as JSON: {exc}\nResponse body: {response_text!r}"
        ) from exc

    if isinstance(response, dict):
        if "prompt_id" in response:
            return response["prompt_id"]
        if "id" in response:
            return response["id"]

    if isinstance(response, str) and response:
        return response

    raise RuntimeError(f"Unexpected /prompt response format: {response}")


def collect_images(ws: websocket.WebSocket, prompt_id: str) -> list[tuple[str, bytes]]:
    output_images: list[tuple[str, bytes]] = []
    current_node = ""
    while True:
        out = ws.recv()
        if isinstance(out, str):
            message = json.loads(out)
            msg_type = message.get("type")
            if msg_type == "executing":
                data = message.get("data", {})
                if data.get("prompt_id") == prompt_id:
                    if data.get("node") is None:
                        break
                    current_node = data.get("node", "")
            elif msg_type == "execution_error":
                raise RuntimeError(message.get("data"))
        elif isinstance(out, (bytes, bytearray)):
            if current_node:
                output_images.append((current_node, bytes(out[8:])))
    return output_images


def sanitize_prompt_name(prompt_name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "_", prompt_name.strip())
    return sanitized[:64].rstrip("_") or "prompt"


def output_image_path(output_dir: Path, workflow_name: str, prompt_name: str, node_name: str | None = None) -> Path:
    folder = output_dir / workflow_name
    if node_name:
        return folder / f"{workflow_name}_{sanitize_prompt_name(prompt_name)}_{sanitize_prompt_name(node_name)}.png"
    return folder / f"{workflow_name}_{sanitize_prompt_name(prompt_name)}.png"


def find_existing_pngs(output_dir: Path, workflow_name: str) -> list[Path]:
    folder = output_dir / workflow_name
    if not folder.exists():
        return []
    return sorted(folder.glob(f"{workflow_name}_*.png"))


def compute_stamp_hash(workflow: dict, prompt: str, seed: int) -> str:
    """Compute a deterministic hash from the workflow JSON, prompt text and seed."""
    workflow_bytes = json.dumps(workflow, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    prompt_bytes = prompt.encode("utf-8")
    seed_bytes = str(seed).encode("utf-8")
    h = hashlib.sha256()
    h.update(workflow_bytes)
    h.update(b"||PROMPT||")
    h.update(prompt_bytes)
    h.update(b"||SEED||")
    h.update(seed_bytes)
    return h.hexdigest()


def read_stamp_from_png(png_path: Path) -> str | None:
    try:
        with Image.open(png_path) as img:
            return img.info.get(STAMP_PNG_KEY)
    except Exception:
        return None


def save_png_with_stamp(png_path: Path, image_data: bytes, stamp_hash: str) -> None:
    with Image.open(BytesIO(image_data)) as img:
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text(STAMP_PNG_KEY, stamp_hash)
        img.save(png_path, format="PNG", pnginfo=pnginfo)


def find_prompt_images(output_dir: Path, prompt_name: str) -> list[tuple[bytes, str]]:
    sanitized = sanitize_prompt_name(prompt_name)
    items: list[tuple[bytes, str]] = []
    for workflow_dir in sorted(output_dir.iterdir()):
        if not workflow_dir.is_dir():
            continue
        # match files like <workflow>_<sanitized>.png or <workflow>_<sanitized>_<node>.png
        matching = sorted(workflow_dir.glob(f"{workflow_dir.name}_{sanitized}*.png"))
        if not matching:
            continue
        items.append((matching[0].read_bytes(), workflow_dir.name))
    return items


def prompt_label_from_png(png_path: Path, workflow_name: str, prompt_titles: list[str]) -> str:
    name = png_path.name
    # Try to find which prompt title this filename corresponds to by matching the sanitized prompt name.
    for prompt in prompt_titles:
        san = sanitize_prompt_name(prompt)
        pattern = fr"^{re.escape(workflow_name)}_{re.escape(san)}(?:_.+)?\.png$"
        if re.match(pattern, name):
            return prompt
    return png_path.stem


def create_prompt_overviews(prompts: list[dict], output_dir: Path) -> int:
    total = 0
    for entry in prompts:
        title = entry.get("title")
        if not title:
            continue
        image_items = find_prompt_images(output_dir, title)
        if not image_items:
            continue

        prompt_output = output_dir / f"Prompt_{sanitize_prompt_name(title)}.jpg"
        create_collage(image_items, title, prompt_output)
        print(f"Saved prompt overview: {prompt_output}")
        total += 1
    return total


def process_workflows(prompts: list[dict], workflows_dir: Path, output_dir: Path, timeout: float, seed: int, skip_hash_verify: bool = False) -> int:
    workflow_files = sorted(workflows_dir.glob("*.json"))
    if not workflow_files:
        raise FileNotFoundError(f"No workflow JSON files found in {workflows_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    ws = None
    try:
        for workflow_file in workflow_files:
            workflow_data = load_workflow(workflow_file)
            workflow_name = workflow_file.stem
            final_output_path = output_dir / f"Workflow_{workflow_name}.jpg"
            prompt_titles = [entry.get("title") or f"Prompt_{i+1}" for i, entry in enumerate(prompts)]

            # Prepare containers
            workflow_images: list[tuple[bytes, str]] = []
            cached_titles: set[str] = set()
            cached_prompt_indices: set[int] = set()

            # For each prompt, check whether a PNG exists and contains a matching embedded stamp.
            for prompt_index, entry in enumerate(prompts, start=1):
                title = entry.get("title")
                prompt = entry.get("prompt")
                if title is None or prompt is None:
                    continue

                png_path = output_image_path(output_dir, workflow_name, title)
                if png_path.exists():
                    try:
                        if skip_hash_verify:
                            # In skip-hash mode, just use the file if it exists
                            label = prompt_label_from_png(png_path, workflow_name, prompt_titles)
                            print(f"Using cached PNG for prompt '{title}' for workflow '{workflow_name}' (hash verification skipped)")
                            workflow_images.append((png_path.read_bytes(), label))
                            cached_titles.add(label)
                            cached_prompt_indices.add(prompt_index)
                        else:
                            # Verify hash matches before using cached file
                            desired_hash = compute_stamp_hash(workflow_data, prompt, seed)
                            stamp_hash = read_stamp_from_png(png_path)
                            if stamp_hash == desired_hash:
                                label = prompt_label_from_png(png_path, workflow_name, prompt_titles)
                                print(f"Using cached PNG for prompt '{title}' for workflow '{workflow_name}'")
                                workflow_images.append((png_path.read_bytes(), label))
                                cached_titles.add(label)
                                cached_prompt_indices.add(prompt_index)
                    except Exception:
                        # if anything goes wrong reading embedded stamp, treat as not cached and regenerate
                        pass

            if len(cached_titles) >= len(prompts):
                print(f"Regenerating Workflow_{workflow_name}.jpg from existing PNGs for workflow '{workflow_name}'")
                create_collage(workflow_images, workflow_name, final_output_path)
                print(f"Saved workflow overview: {final_output_path}")
                total += 1
                continue

            if cached_titles:
                print(f"Found {len(cached_titles)} cached PNG(s) for workflow '{workflow_name}'; will generate missing titles")

            # Generate missing prompts
            for prompt_index, entry in enumerate(prompts, start=1):
                title = entry.get("title")
                prompt = entry.get("prompt")
                if title is None or prompt is None:
                    continue

                # Skip prompts already present in cache (verified by stamp)
                if prompt_index in cached_prompt_indices:
                    print(f"Skipping cached prompt '{title}' for workflow '{workflow_name}'")
                    continue

                prompt_workflow = apply_prompt_to_workflow(workflow_data, prompt, seed=seed)

                if ws is None:
                    ws = connect(timeout=timeout)

                prompt_id = queue_prompt(prompt_workflow, timeout=timeout)
                print(f"Queued workflow '{workflow_name}' with prompt '{title}': prompt_id={prompt_id}")
                images_by_node = collect_images(ws, prompt_id)

                if not images_by_node:
                    raise RuntimeError(f"No images received for prompt {prompt_id}")

                # Compute stamp hash for this prompt/workflow so it can be written for each PNG produced
                desired_hash = compute_stamp_hash(workflow_data, prompt, seed)
                multiple_nodes = len(images_by_node) > 1

                for node_name, image_data in images_by_node:
                    workflow_images.append((image_data, title))
                    node_suffix = None
                    if multiple_nodes:
                        node_suffix = sanitize_prompt_name(node_name or "output")
                    png_path = output_image_path(output_dir, workflow_name, title, node_suffix)
                    png_path.parent.mkdir(parents=True, exist_ok=True)
                    save_png_with_stamp(png_path, image_data, desired_hash)
                    print(f"Saved PNG cache: {png_path}")

                total += 1

            if not workflow_images:
                raise RuntimeError(f"No images available to create workflow overview for workflow '{workflow_name}'")

            create_collage(workflow_images, workflow_name, final_output_path)
            print(f"Saved workflow overview: {final_output_path}")

        prompt_overview_count = create_prompt_overviews(prompts, output_dir)
        if prompt_overview_count:
            print(f"Saved {prompt_overview_count} prompt overview(s).")
    finally:
        if ws is not None:
            ws.close()

    return total


def main() -> None:
    global SERVER

    parser = argparse.ArgumentParser(
        description="Submit workflow/prompt combinations to a ComfyUI server and save result images."
    )
    parser.add_argument(
        "--prompts-file",
        type=Path,
        default=Path(__file__).resolve().parent / "prompts.md",
        help="Path to the prompts.md file.",
    )
    parser.add_argument(
        "--workflows-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "workflows",
        help="Directory containing workflow JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "outputs",
        help="Directory where generated files are saved.",
    )
    parser.add_argument(
        "--server",
        type=str,
        default=SERVER,
        help="Server address in host:port format.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="Network timeout in seconds for HTTP and websocket operations. Set 0 for no timeout on long-running jobs.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=FIXED_SEED,
        help="Seed value to use for all generated images.",
    )
    parser.add_argument(
        "--skip-hash-verify",
        action="store_true",
        help="Skip hash verification on regeneration; only check if file exists (faster for debugging).",
    )
    args = parser.parse_args()
    if args.timeout < 0:
        parser.error("--timeout must be zero or a positive number")
    SERVER = args.server

    prompts, prefix_text, postfix_text = load_prompts(args.prompts_file)
    if prefix_text or postfix_text:
        write_expanded_prompts_file(args.prompts_file, prompts, args.output_dir)

    try:
        count = process_workflows(prompts, args.workflows_dir, args.output_dir, args.timeout, args.seed, args.skip_hash_verify)
    except ConnectionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Processed {count} workflow/prompt combinations.")


if __name__ == "__main__":
    main()
