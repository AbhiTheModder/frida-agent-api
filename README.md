***lightweight, simple frida agent compiler & API***

<img width="796" height="616" alt="image" src="https://github.com/user-attachments/assets/58294805-e723-44e8-bcdb-ac7f120a686e" />

## Requirements

- Python 3.x
- A Unix-like environment (Linux, macOS, or WSL)

This project does not require any manual package installation via pip. The only external dependency, [bottle.py](https://bottlepy.org/docs/dev/), is a single-file library that the start script downloads automatically on the first run.

## Setup and Usage

To get started, clone the repository and execute the start script. The script handles everything for you.

  ```shell
  git clone https://github.com/AbhiTheModder/frida-agent-api
  cd frida-agent-api
  ./start # or bash start
  ```

Now you can either visit the frontend page to use as GUI or API to use in your project, scripts, CLI:
1. **Sending a TS File**
   ```shell
    curl -X POST "http://localhost:8000/compile" \
    -F "file=@/path/to/my/script.ts" \
    --output _agent.js
    ```
2. **Sending a Text Snippet**
   ```shell
    curl -X POST "http://localhost:8000/compile" \
    -F "snippet=console.log('Hello from Frida Agent');" \
    --output _agent.js
    ```

## F.A.Q

1. **Why?**
Modern Frida versions require a tedious setup: initializing projects with `frida-create`, managing bridges (*[Starting with Frida 17.0.0, bridges are no longer bundled with Frida’s GumJS runtime](https://frida.re/docs/bridges/)*), and manual compilation. These steps are often impossible(at the time of writing this) in environments like **Termux**, where `frida-compile` frequently fails.

**This project automates the entire workflow**. Simply provide your script, and the tool handles project creation, dependency management, and compilation for you.
