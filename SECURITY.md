# Security Policy

## Supported versions

`ldraw-mcp` ships fixes against the latest released version only. The current
release is **v0.2.3**. Please reproduce any issue on the latest release
(`uvx ldraw-mcp` always pulls it) before reporting.

| Version        | Supported          |
| -------------- | ------------------ |
| latest (0.2.x) | :white_check_mark: |
| < latest       | :x:                |

## Reporting a vulnerability

**Please do not open a public issue for a security vulnerability.**

Report privately, either way:

- Preferred: use GitHub's **"Report a vulnerability"** button under the repo's
  **Security** tab (private security advisories), or
- Email **mjarnold1998@gmail.com**.

Please include a description of the issue, the affected version, and a minimal
reproduction — the tool call, and the `.ldr`/`.dat` input if you can share it.
You can expect an initial acknowledgement within a few days. Once a fix ships,
you'll be credited in the release notes unless you ask otherwise.

## Security model

This server **launches Blender as a subprocess** and feeds it model files. Both
halves of that are worth understanding before you deploy it.

**On the subprocess itself:**

- The command is built as an **argv list and run without a shell**
  (`subprocess.run(cmd, ...)`, no `shell=True`), so there is no shell-quoting or
  command-injection surface. Caller-supplied values arrive as separate argv
  entries, not as text spliced into a command line.
- The Blender script it executes is **bundled with the package**
  (`blender_script.py`, resolved relative to the module), never supplied by the
  caller. Blender is invoked with `--factory-startup`, so the operator's own
  Blender configuration and any startup add-ons are not loaded.
- **Which Blender binary runs is an operator decision, not a caller one**: it comes
  from the `LDRAW_MCP_BLENDER` environment variable or a `PATH` lookup. A caller
  cannot point the server at a different executable.
- Renders are bounded by a **timeout**, and `LDRAW_MCP_DISABLE=1` disables
  rendering entirely — useful when you want the server present but inert.

**On the input, which is the part that actually deserves care:**

- `render_ldraw_file` takes a **path from the caller** and reads it from the host
  filesystem. There is no directory allow-list and no sandbox. Any file the
  operating-system user running the server can read may be handed to Blender.
- That input is then parsed by **Blender and the ImportLDraw addon** — a large
  native codebase that was not written with hostile input in mind. A malicious
  `.ldr`/`.dat` is the realistic threat here, and it is a threat against Blender
  rather than against this server's Python.
- `render_ldraw_text` writes caller-supplied text to a temporary directory and
  renders that, so the same parser exposure applies without the arbitrary-read one.

Practical guidance: run it as a user whose read access you are comfortable
exposing to the model driving it, do not run it as root, and treat `.ldr` files
from untrusted sources the way you would treat any untrusted input to a native
parser.

**Out of scope:** rendering a file the caller explicitly asked to render is
working as documented. **In scope:** reading a path the caller did not ask for,
escaping the running user's own permissions, executing caller-controlled code in
the Blender subprocess, or any command-injection path into the argv list.
