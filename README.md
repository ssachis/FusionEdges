# FusionEdges

**Compatibility-aware UI component transplantation with AI coding agents**

FusionEdges investigates a practical software-engineering question: **does giving an AI coding agent an explicit compatibility report make it better at transferring a UI component from one repository into another?**

Imagine transferring a navbar from a donor React application into an existing host website. Copying JSX alone may not work: the navbar may depend on different packages, aliases, styles, routing conventions, browser scripts, or framework-specific rules. FusionEdges inspects both repositories, prepares the code context, and compares two ways of asking an AI agent to perform the integration.

> **Current focus:** adding one component (for example, a navbar) to an existing host repository. Broader codebase migration and style transfer remain in the codebase but are not the current primary UI workflow. The system has static/build checks; automated browser/runtime verification is **planned**, not yet established in the supplied project description.

## Research question

**Does a structured compatibility report improve the correctness and efficiency of AI-assisted UI component transplantation compared with an otherwise equivalent coding-agent baseline?**

| Condition | Coding agent receives |
| --- | --- |
| **A — Agent only** | The task prompt and selected host/donor code |
| **D — Agent + report** | The same prompt and selected code, **plus** a compact compatibility report |

The extra report is the intended experimental intervention. A comparison should keep the repository revisions, target component, coding model, context selection, limits, and verification procedure consistent. See [EXPERIMENTS.md](docs/EXPERIMENTS.md).

## How it works

```text
Host repository + donor repository + target component
                         |
                Clone and scan repos
                         |
             Select component + imports
             + host integration points
                         |
                +--------+--------+
                |                 |
             A: baseline       D: report
             Same code          Same code
             + prompt           + prompt
                |                + compatibility analysis
                |                 |
                +--------+--------+
                         |
                 Coding agent edits
                         |
                  Build and checks
                         |
                Compare paired results
```

For component tasks, the described implementation **deterministically** locates the donor component, traces imports up to two hops (including configured aliases), and selects relevant host wiring files. This is distinct from the BM25-based retrieval described for other/older workflows. The report captures concrete integration issues such as missing packages, mount locations, and host scripts that depend on CSS selectors.

By default, editing is a one-shot code generation pass with up to two build-driven repairs. A multi-step agent mode is also available. Detailed mechanisms: [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Getting started

**Prerequisites:** Python 3.9+, Git, Node.js/npm for building and running generated projects, and an API key for at least one supported model provider. The project description says the Python server does not require additional pip packages.

```bash
# Set one provider key, e.g.:
export GROQ_API_KEY="YOUR_KEY"
# Other documented options include GEMINI_API_KEY,
# NVIDIA_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY.

python3 -m fusionedges.server
```

Open **http://127.0.0.1:8000**. In the current UI, supply the **host** repository, **donor** repository, and component name (e.g. `navbar`); run both **Prompt only** and **Prompt + compatibility report** for a comparison. Run results are saved locally. The interface labels and available options may differ by checkout/version.

### Command-line examples

```bash
python3 -m fusionedges providers
python3 -m fusionedges scan --source you/my-site --donor someone/donor-site

# A: agent-only component integration
python3 -m fusionedges run --source you/my-site --donor someone/donor-site --component navbar

# D: same task with compatibility report
python3 -m fusionedges run --source you/my-site --donor someone/donor-site --component navbar --report
```

Replace example repository identifiers with actual accessible repositories. Confirm command-line options against your checked-out version before running a study.

For a reproducible comparison, pin **one** provider and model instead of relying on cross-provider fallback:

```bash
export FUSIONEDGES_PROVIDERS=anthropic
# Configure your chosen provider/model and API key consistently for all paired runs.
```

See [DEVELOPMENT.md](docs/DEVELOPMENT.md) for configuration, tests, logs, and troubleshooting.

## Outputs and current checks

The documented run layout includes:

```text
examples/                 # cached/cloned repositories
output/
  runs.db                 # run and report-conflict records
  <run-id>/
    project/              # generated project (local Git repository)
    project.zip
    report.md             # report condition only
    report.json           # report condition only
    checks.json
    facts.json
    build.log
    run.json
```

The system reports build/static checks. For component tasks, the source README describes checks for component addition, framework-safe integration, package/assets presence, duplicate navbars, and successful build. **Build success does not prove that the UI renders or behaves correctly in the browser.** The next evaluation milestone is browser and functional checks.

## Evaluation status

The documentation describes a working execution pipeline and automated tests, but **does not provide a completed, independently verified real-provider benchmark**. We do not claim that condition D outperforms A. The proposed study will evaluate paired outcomes, including build success, component visibility/function, host regression, runtime errors, token usage, cost, and failure types. See [EXPERIMENTS.md](docs/EXPERIMENTS.md).

## Documentation

| File | Purpose |
| --- | --- |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Data flow, scanning, report generation, editing, checks, and trust boundaries |
| [EXPERIMENTS.md](docs/EXPERIMENTS.md) | Hypothesis, controlled comparisons, benchmark protocol, metrics, and reporting |
| [DEVELOPMENT.md](docs/DEVELOPMENT.md) | Local setup, commands, config, troubleshooting, and contributor workflow |
| [ROADMAP.md](docs/ROADMAP.md) | Prioritized milestones, acceptance criteria, and two-person ownership |

## Safety

Treat cloned repositories as untrusted. **`npm install`, `npm run build`, and dev scripts execute repository-controlled code and are not sandboxed by FusionEdges.** Only execute trusted repositories in an appropriately isolated environment. Keep provider keys in local environment variables; never commit credentials. The described system limits agent writes to the generated project directory and does not automatically push output to remote repositories.

## Project scope and limitations

- Current primary workflow: **individual UI component transplantation**.
- Static/build checks are implemented according to the supplied project description; **browser/runtime checks remain planned**.
- Models may produce incorrect integrations even when code builds.
- Report generation incurs additional calls/tokens; usefulness and overall cost must be measured, not assumed.
- Findings and implementation details in these docs are based on the supplied README, not a fresh source-code audit.
