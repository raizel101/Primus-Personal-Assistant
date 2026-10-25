# Formal planning in PRIMUS

## What this increment implements

PRIMUS now includes a grounded, positive-precondition **STRIPS planner**. Unlike the existing LLM's prose task plan, this planner represents a finite state as a set of true facts, checks action preconditions and searches action sequences that satisfy a declared goal. It uses no LLM, packages, internet or cloud service.

For state `S` and an action with preconditions `P`, add effects `A` and delete effects `D`:

- The action is applicable only if `P` is a subset of `S`.
- Its predicted next state is `(S - D) union A`.
- Every goal fact must be present in the final state.
- Facts absent from the state are false in this closed-world model.

Breadth-first graph search returns a shortest **unit-cost** plan when it finds one. A separate replay starts from the initial state and checks every returned transition and the final goal. Search remembers visited states to avoid cycles. Empty plans are valid if the initial state already satisfies the goal.

**A symbolic plan does not execute anything.** The examples describe modeled transitions; they do not move a download, write code, run a script or grant permission. Even a fact named `approved` would be ordinary model data, never human consent. The current planner does not automatically turn an arbitrary natural-language request into a trustworthy world model. The supplied facts and effects must be reviewed for the intended task.

## Try it on this Windows laptop

```powershell
$primusPython = 'C:\Users\Subhajeet Bose\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$primusRoot = 'C:\Users\Subhajeet Bose\Downloads\Primus'
& $primusPython "$primusRoot\primus.py" planning solve "$primusRoot\examples\planning\downloads.json"
& $primusPython "$primusRoot\primus.py" planning validate "$primusRoot\examples\planning\downloads.json" --actions create_folder stage_download finish_organization
```

The first command returns this action order:

```text
create_folder
stage_download
finish_organization
```

The result includes the initial state, goal, action sequence, replay trace, final state, source path and SHA-256 hashes. Trace states are **predicted**, not observations of the laptop. `executed` is always false. Changing the model changes its hash; a prior proof should not be applied to a new source version.

On macOS/Linux, move/configure the full root and use the same commands with the chosen Python executable, for example:

```sh
python3 '/path with spaces/Primus/primus.py' planning solve '/path with spaces/Primus/examples/planning/downloads.json'
```

This module uses only Python's standard library and has no OS-specific execution dependency. This increment was tested on the current Windows laptop; it is not evidence of full PRIMUS cross-platform feature parity.

### Use the same core from a task prompt

After the background service loads the updated code, type:

```text
planning: examples/planning/downloads.json
```

`strips:` is an alias. This explicit route reads the exact file path and bypasses model inference and fast general-answer routing. A quoted absolute path also works. Existing CLI `ask`, tray/API tasks and other task clients share this core route. Voice can reach the route through the same service, but pronouncing a full JSON path is inconvenient; a new voice shortcut is not implemented.

The tools `formal_plan` and `formal_validate` are auto-discovered from `plugins/planning.py`. They are local READ tools, subject to approved-folder/credential guards and the configured READ policy. General agent requests can use them when supplied a model path. Each invocation is audited. A model's explanation cannot replace the tool's actual result. The existing planner/coder/verifier mission workflow remains distinct; this increment does not claim a formally verified controller for every mission or real-world task.

## JSON problem format

Each problem has exactly these fields:

```json
{
  "name": "Prepare a report",
  "facts": ["source_present", "draft_ready", "report_ready"],
  "initial": ["source_present"],
  "goal": ["report_ready"],
  "actions": [
    {
      "name": "draft_report",
      "preconditions": ["source_present"],
      "add": ["draft_ready"],
      "delete": []
    },
    {
      "name": "finish_report",
      "preconditions": ["draft_ready"],
      "add": ["report_ready"],
      "delete": []
    }
  ]
}
```

- Facts and action names are lowercase identifiers matching `[a-z][a-z0-9_-]{0,47}`.
- Every referenced fact must be declared. Duplicates, unknown fields and duplicate JSON keys are rejected.
- Actions contain exactly `name`, `preconditions`, `add` and `delete`. Add/delete lists cannot overlap.
- Preconditions and goals are conjunctions of positive facts. Multiple goals must all hold.
- Actions are already **grounded**: concrete action names and facts are supplied, not parameterized predicates or variables.
- The model cannot include executable commands, imports, tool bindings or permission callbacks.

## Outcomes and limits

| Result | Meaning |
| --- | --- |
| `solved` | A plan reached the goal and passed replay under the supplied model. |
| `unreachable` | The complete reachable state graph was exhausted without satisfying the goal. |
| `limit_reached` | Time, state or depth bound truncated the search. Reachability remains unknown. |
| Invalid input / cancellation error | No plan was accepted. The kill switch and task cancellation stop work. |

Input must be an approved UTF-8 JSON file of at most 64 KB. Limits are 32 facts, 64 actions, 4,096 visited states, depth 16 and two seconds of search. Replay accepts at most 16 actions. The CLI returns exit code 0 for a solved plan or valid replay, 2 for an unsolved/limited plan or invalid replay, and 1 for input/runtime errors. An invalid replay reports the first failed step or remaining goals.

Current exclusions: full PDDL syntax/parser/export, automatic grounding, negative preconditions/goals, conditional effects, numeric/temporal planning, action costs, probabilities, partial observability, HTN planning and direct plan execution. A symbolic proof holds only under the model assumptions; it is not proof of program correctness or real laptop state.

## Verification

- All **173** automated tests passed after this implementation, including **14** new formal planning tests.
- `scripts/demo-formal-planning.py` exercises three actual authenticated HTTP task requests through a temporary localhost API, closes it afterward and saves `outputs/formal-planning-verification.json`.
- Download staging: solved in three steps and independently replayed.
- Nominal coding workflow: solved in four steps and independently replayed. Its `review_passed` effect is an assumption for this example, not an actual code review.
- Locked document with no unlock action: exhaustively unreachable.
- Additional checks reject invalid ordering, verify the real CLI, compare source hashes and confirm no source modification.

The examples use real planning code and real local transport. Their domains are deliberately supplied fixtures, and their actions are not executed. No new script-execution approval is needed to compute these READ plans under the default READ policy; any later actual laptop action needs its normal authorization.

## Syllabus connection and next step

This implements the syllabus's STRIPS-style symbolic state/action/precondition/effect planning topic. It adds formal search and validation alongside PRIMUS's existing LLM planning, MCP and lexical RAG. It does not yet implement the syllabus's full PDDL representation.

A subsequent increment can add a strict supported PDDL subset and validated translation into this internal model. Connecting plans to real tools would additionally require reviewed action bindings, observed-state checks and fresh exact approval at each sensitive action. Plan computation must never be reused as blanket execution approval.
