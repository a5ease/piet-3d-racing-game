# Piet 3D Racing Game

**English | [中文](README.md)**

> **A word from the author**: After finishing a previous project (a "Power Glide"-style game),
> I stumbled upon Piet — an esoteric programming language that deeply fascinated me.
> The more I learned about it, the more I realized it didn't seem capable of doing 3D games.
> And so this project was born out of a single question: **can Piet actually build a 3D game?**

> Please note: all the code was AI-generated. The human part went into the design decisions,
> and into the drawing on the whiteboard area below the main code (I can't draw,
> so I spent a long time with a mouse 💦).

> I know being AI-generated might draw some criticism online. Please go easy on me 😭

![Gameplay screenshot](image.png)

A 3D racing demo where **every decision lives inside a Piet program image**. Python only
acts as Piet's "hands": it runs the interpreter (VM), handles window/keyboard I/O, and
renders the values Piet computes into pixels. **All game logic (player position, speed,
collision, laps, biomes, camera pose) is computed at runtime by Piet instructions.**

## Controls

| Key | Action |
|---|---|
| `←` / `→` | Turn left / right |
| `↑` / `↓` | Accelerate / decelerate |
| `Esc` or close window | Quit |

The HUD shows a speedometer, a rev counter, a timer, and a minimap. The track passes
through several biomes (city / beach / hills, etc.) — watch out for trees and buildings.

## Requirements

- Python 3.8+ (3.11 recommended), Windows / macOS / Linux
- Dependencies are listed in `requirements.txt`. Install with:

```bash
pip install -r requirements.txt
```

## Run

```bash
python run_game.py
```

On startup it regenerates `game_core.png` (the Piet program itself, 450k+ codels) while
preserving the artwork from the previous session, then enters the main game loop.

## Project Structure

| File | Purpose |
|---|---|
| `racing_game.py` | Engine core: Piet assembler, Piet interpreter, OpenGL renderer, HUD |
| `piet_asm.py` | Piet assembly standard library (fixed-point sin, LCG, loop/branch macros) |
| `paint_core.py` | Tool for drawing in the program image's artwork area (by hand) |
| `run_game.py` | One-click entry point |
| `game_core.png` | The generated Piet program itself (the source of truth for decisions) |

> By "pure Piet-driven" I mean this 1803×1803 image: `game_core.png` *is* the "source code"
> of the game logic. At runtime the interpreter walks the codel blocks one by one and
> executes the instructions inside — every game decision is computed in this image.

![Piet program (source of truth)](game_core.png)

## Debug Environment Variables (for automation, use with care)

| Variable | Purpose |
|---|---|
| `PIET_FORCE_2D=1` | Force the software raster (2D) fallback channel (when OpenGL is unavailable) |
| `PIET_SEEK_Z=<int>` | Jump to a specific track position |
| `PIET_DEMO_STEER=1` | Simulate steering automatically, for headless screenshot verification |

The `verify_*.py` / `smoke_*.py` scripts under the `痛苦旅程/` directory are milestone
regression tests used during development.