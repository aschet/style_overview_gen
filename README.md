# style_overview_gen

`style_overview_gen` submits ComfyUI workflows to a running ComfyUI-compatible server, renders each workflow with a set of prompts, and builds overview images.

<p align="center">
  <img src="/example.jpg?raw=true">
</p>

## What It Does

- Loads `prompts.json` containing prompt titles and prompt text
- Loads workflow definitions from `workflows/*.json`
- Applies each prompt to each workflow
- Sends the workflows to ComfyUI for rendering
- Saves generated PNG outputs into a workflow-specific subfolder
- creates a JPG overview collage of all outputs per workflow

## Requirements

- ComfyUI must be installed and available on the local machine
- All models required by the workflows must be installed in ComfyUI
- ComfyUI must be started before running this program

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python style_overview_gen.py
```

Optional arguments:

- `--prompts-file` — path to the prompts JSON file
- `--workflows-dir` — directory containing workflow JSON files
- `--output-dir` — root output directory
- `--server` — server host:port (default `127.0.0.1:8188`)
- `--timeout` — network timeout in seconds

## Output Structure

- raw PNG outputs are written to `outputs/<workflow_name>/`
- PNG filenames include workflow name, prompt index, and sanitized prompt title
- a model overview is saved as `outputs/Model_<workflow_name>.jpg`
- a prompt overview is saved as `outputs/Prompt_<prompt_title>.jpg`

## Workflow Preperation

The workflows included in this repository are derived from the ComfyUI workflow templates. To prepare a workflow for use with the ComfyUI API and this program, you must save it using "Export (API)". This option is available only when Dev Mode is enabled in the settings. Additionally, a "Text String (Multi Line)" node named "Prompt" must be added to receive the text prompt, an "Int" node named "Seed" must be added to receive the random seed, and a "Save Image (Websocket)" node must also be added. These nodes must be connected in a manner similar to the example shown below.

<p align="center">
  <img src="/workflow.png?raw=true">
</p>
