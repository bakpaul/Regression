# Weaknesses

A static audit of the module: everything below comes from reading the sources, not from
running the program. Findings that depend on SOFA runtime behaviour are marked as such.

Tier 1 — **Critical** — is restricted to the two failure modes that defeat the purpose of a
regression suite:

- **false pass** — a scene that has actually regressed is reported as passing, or the exit
  code stays 0 when something failed;
- **silent loss of coverage** — scenes, mechanical objects or keyframes that were supposed to
  be checked are not checked, and nothing says so.

Tier 2 — **Other** — everything else, ordered by decreasing severity.

---

# Tier 1 — Critical

## C1. A diverged simulation passes: `NaN` defeats the verdict

`tools/RegressionSceneData.py:393-398` (and identically `:523-525` for legacy)

```python
if self.error_by_dof[meca_id] > self.epsilon:
    self.regression_failed = True
    return False
return True
```

Every IEEE comparison with `NaN` is False, so `nan > epsilon` is False and the function
returns `True`. If the simulation produces a single `NaN` position — the classic symptom of a
diverging solver, an exploding constraint, an uninitialised mass — then `data_ref - meca_dofs`
contains `NaN`, `np.linalg.norm` returns `NaN`, `error_by_dof` becomes `NaN`, and **the scene
is reported as a success**. `regression_failed` also stays `False`, so `log_errors()`
(`:99-109`) prints a green `[Regression-Success]` line with a frame count, and the exit code
is 0.

This is the worst failure mode in the module: the one numerical outcome that most clearly
signals a broken simulation is the one outcome that cannot fail the test.

It is also self-perpetuating in the other direction. If references are generated from an
already-diverged run (`--write-references` has no sanity check whatsoever on what it writes,
`:234-243`), the stored reference contains `NaN`; from then on `data_ref - meca_dofs` is `NaN`
regardless of what the simulation does, and that scene can **never fail again**.

Note the asymmetry with `inf`: `inf > epsilon` is True, so an infinite position does fail
correctly. Only `NaN` is silent — including the `inf - inf` case, which produces `NaN`.

*Fix sketch:* after the accumulation loop, reject non-finite values explicitly —
`if not np.isfinite(self.error_by_dof[meca_id]): regression_failed = True; return False` —
and refuse to write a reference containing non-finite positions.

**DECISION**: Apply this fix with a light diff as you sketched it here.

## C2. A scene with no `MechanicalObject` passes

`tools/RegressionSceneData.py:252` and `:393-398`

`nbr_meca = len(self.meca_objs)` is taken from the **current** scene. If `parse_node`
(`:130-141`) collects nothing, then:

- the reference-loading loop (`:273`) never executes, so no missing-file error is raised;
- `keyframes` stays `[]`, so `nbr_frames == 0` and no frame is ever compared;
- the verdict loop `for meca_id in range(0)` does not execute;
- the function returns **`True`**.

`compare_legacy_references` has exactly the guard that is missing here (`:509-511`):

```python
if nbr_meca == 0:
    self.regression_failed = True
    return False
```

The non-legacy path — the only one the CLI can reach — does not.

This is reachable without anyone editing the scene: `parse_node` keeps a mechanical state only
if `is_simulated(node)` is true (the node or an ancestor has an ODE solver) and the
`meca_in_mapping` / `is_mapped` filter allows it. So a scene where a required plugin fails to
load, where the solver component is renamed or removed, or where the graph is restructured so
solvers no longer sit above the states, will produce **zero** tested objects and report
success. That is precisely the class of breakage a regression suite exists to catch.

*Fix sketch:* port the `nbr_meca == 0` guard into `compare_references`, and additionally fail
`write_references` when it would write zero files (see C9).

**DECISION**: Apply this fix with, both of your suggested solution in your sketch are OK.


## C3. "No frames were tested" is printed as an error but does not fail the run

`tools/RegressionSceneList.py:238-240` vs `tools/RegressionSceneData.py:106-107`

There *is* a safety net for C2 — `log_errors()` prints
`[Regression-Error] No frames were tested for <scene>` when `nbr_tested_frame == 0`. But it is
purely cosmetic: `apply_result` only increments `nbr_errors` when `result["result"]` is falsy,
and in the C2 scenario `result["result"]` is `True`.

So the run prints a red error line, prints `### Number of scenes failed: 0`, and exits 0. Any
CI job gating on the exit code — the normal way to use this tool — treats it as a pass. Worse,
the two outputs contradict each other, which invites a human to trust the summary.

The same mismatch happens for every early `return False` in `compare_references` (missing
reference file `:324-326`, missing CSV metadata `:327-329`, reference size mismatch `:289-295`,
timeline mismatch `:306-311`, shape mismatch `:353-359`): these return `False` **without
setting `regression_failed = True`**. The exit code is then correct (`nbr_errors` is
incremented), but `log_errors()` falls through to the `else` branch and prints
`[Regression-Success]` for a scene that just failed structurally. Failure and success are
reported for the same scene in the same run.

*Fix sketch:* make `nbr_tested_frame == 0` a real failure in `apply_result`, and set
`regression_failed = True` (or introduce a distinct `structural_failure` flag) on every early
`return False` so the per-scene log agrees with the summary.

**DECISION**: This fix is needed but I need a clearer plan for the fix to apply it

## C4. `steps` smaller than the reference silently tests only a prefix

`tools/RegressionSceneData.py:334-390`

The comparison loop runs `for step in range(0, self.steps + 1)` and consumes reference
keyframes as the simulated time reaches them. If the reference file holds keyframes beyond
`dt * steps`, the loop simply ends and the remaining keyframes are never compared. Nothing
warns, `return True` is reached normally, and the scene passes.

Concretely: references written with `steps 1000`, list file later edited to `steps 100` — the
run compares roughly the first 10% of the trajectory and reports success. Since divergence
typically grows with time, the untested tail is exactly the part most likely to have
regressed.

Again the legacy path has the check that is missing here (`:456-457`):

```python
if nbr_frames != self.steps:
    helper.writeWarning(f"Number of steps saved in reference file ({nbr_frames}) does not match ...")
```

`compare_references` has no equivalent — and even in legacy it is only a *warning*, so it
does not fail the run either.

*Fix sketch:* after the loop, fail if `nbr_tested_frame != nbr_frames`. This is the single
cheapest high-value check to add, and it also catches C5 and part of C12.

**DECISION**: I would keep the old behavior : Warning but no error. Regression might increase, but we migh like to reduce the nb of steps without having to regenerate anything.


## C5. Losing a `MechanicalObject` silently reduces coverage

`tools/RegressionSceneData.py:180-187` and `:252`

Reference files are addressed by **position in the traversal order plus name**:

```
<file_ref_path>.reference_mstate_<index>_<mecaObjName>.json.gz
```

and the loop that reads them iterates over the objects present in the *current* scene. There
is no record anywhere of how many objects the reference set was written for (see C12).

Consequence: if a scene loses its **last** collected mechanical object — say objects
`[A, B, C]` become `[A, B]` — then the filenames computed are `_0_A` and `_1_B`, both of which
still exist on disk. The comparison succeeds, `_2_C.json.gz` is silently ignored, and object
`C` is no longer tested at all. No warning, exit 0.

Whether this is caught is pure luck of position: removing `B` instead yields `[A, C]` →
filename `_1_C`, which does not exist → `FileNotFoundError` → correctly reported. So the tool
detects a removal in the middle and misses a removal at the end.

Two related fragilities of the same naming scheme:

- inserting a simulated node **before** existing ones shifts every subsequent index, so all
  the old references stop matching and must be regenerated — an unavoidable full regeneration
  triggered by an unrelated graph edit;
- two mechanical objects sharing the same name at different indices (`_0_X`, `_1_X`) will be
  silently swapped if the traversal order changes; if the two happen to hold similar data, the
  swap passes.

*Fix sketch:* store the object count and the ordered list of names in the reference metadata
(C12) and verify it, or key references by scene-graph path rather than traversal index.

**DECISION**: Let's do this

## C6. `error_by_dof` normalisation makes a fixed `epsilon` meaningless on large models

`tools/RegressionSceneData.py:364-365`

```python
full_dist    = np.linalg.norm(data_diff)
error_by_dof = full_dist / float(data_diff.size)
```

The Frobenius norm is divided by the **number of scalars**, not by its square root. For `N`
points with a uniform per-component error `d`, `full_dist = d·√(3N)` and therefore
`error_by_dof = d/√(3N)` — the metric *shrinks as the model grows*. Arithmetic for a uniform
1 mm error, against the default `epsilon = 1e-4`:

| points | `full_dist` | `error_by_dof` | vs default epsilon |
|---|---|---|---|
| 8 | 0.0049 | 2.0e-04 | fails |
| 1 000 | 0.0548 | 1.8e-05 | passes |
| 10 000 | 0.1732 | 5.8e-06 | passes (17× margin) |
| 100 000 | 0.5477 | 1.8e-06 | passes (55× margin) |

The same physical error therefore fails on a small beam and passes with a wide margin on a
detailed mesh. A fixed threshold reused across scenes of different sizes — which is what the
list files encourage — is not a consistent criterion, and the bias is always in the unsafe
direction: **the bigger and more interesting the model, the harder it is to fail.**

Dividing by `√size` (an RMS error) would make the metric size-independent; a max-norm would be
stricter still. Either change requires regenerating nothing, but it does require re-tuning
every `epsilon` in every list file, so it is a breaking change that has to be done
deliberately.

**DECISION**: For now replace the error by an RMS error, will see for other type or error measurment later.


## C7. The verdict depends on `dump_number_step`, which is not recorded

`tools/RegressionSceneData.py:375-376` and `:393-398`

`total_error` and `error_by_dof` are **accumulated across every compared keyframe** and then
compared, un-normalised, against `epsilon`. The threshold is therefore a budget over the whole
trajectory rather than a per-frame tolerance, and its meaning scales with the number of
keyframes: comparing 100 keyframes accumulates roughly 100× the error of comparing one, for
identical physics.

Two practical consequences:

- raising `dump_number_step` in a list file can flip a scene from pass to fail with no code
  change at all, and lowering it can hide a real regression;
- `epsilon` values are not comparable between scenes that sample at different rates, so there
  is no way to reason about "how strict is this suite".

Because `dump_number_step` is not stored in the reference (C12), there is also nothing to
detect that a reference was written at a different sampling rate than the one now configured.

*Fix sketch:* divide by `nbr_tested_frame` to obtain a mean per-frame error, and/or track the
per-frame maximum alongside the sum — a single catastrophic frame and a uniformly slightly-off
trajectory are currently indistinguishable.

**DECISION**: I would prefere to use the maximum error recorded then compare it to epsilon


## C8. `epsilon = nan` is accepted and disables the check

`tools/RegressionSceneList.py:96-105`

Validation is `float(values[2])` followed by `if epsilon < 0`. Python's `float()` happily
parses `nan`, `NaN`, `inf`, `+infinity`; and `float('nan') < 0` is `False`, so `nan` passes
validation. A list file containing `nan` in the epsilon column then yields
`error_by_dof > nan` → always `False` → the scene **always passes** (the same mechanism as
C1, but triggered by a one-character typo in a text file rather than by the simulation).

`inf` is accepted the same way and has the same effect: an infinite threshold can never be
exceeded. A finite-but-absurd value like `1e30` is likewise accepted with no complaint.

*Fix sketch:* `if not math.isfinite(epsilon) or epsilon < 0: parsing_error(...)`, and consider
warning above some sanity ceiling.

**DECISION**: "JUST DO IT"


## C9. Failed reference writes do not fail the run, and leave stale references in place

`tools/RegressionSceneList.py:226-229` and `SofaRegressionProgram.py:240-244`

In write mode `apply_result` prints an error and returns **without incrementing any counter**:

```python
if task["mode"] == "write":
    if not result.get("ok", False):
        helper.writeError(f"While writing references for {scene.file_scene_path}: ...")
    return
```

and `__main__` only consults `nbr_error_in_sets()` when `args.write_mode is False`. So
`--write-references` exits 0 even when every single scene failed to produce a reference.

The dangerous part is the interaction with the previous references. `write_references`
(`:234-243`) writes each file in place, and a scene that crashes before that point leaves the
**old** reference file untouched. The operator sees a successful exit, assumes the references
are current, and every subsequent compare run passes that scene against a stale reference —
potentially for months. This is a false pass with an arbitrarily long lifetime.

The same path also silently accepts writing *zero* files when the scene has no mechanical
objects (C2), and there is no verification that the files that were supposed to be written now
exist on disk.

*Fix sketch:* count write failures, exit non-zero, and delete or rename the previous reference
files of any scene whose write failed so a later compare cannot succeed against stale data.

**DECISION**: OK, and just delete any artifact after one scene ref writting failed for that specific scene (don't delete all references)


## C10. A wrong `--input` or an over-restrictive `--filter` reports success with zero tests

`SofaRegressionProgram.py:37-45`, `190-194`, `234-249`; `tools/RegressionSceneList.py:185-188`

`RegressionProgram.__init__` walks the input folder and collects whatever it finds. Nothing
checks that the folder exists, that it contains any `.regression-tests` file, or that any
scene survived filtering. `os.walk` on a non-existent path yields nothing and raises nothing.

So a typo in `--input`, a wrong working directory, a moved test tree, or a `--filter` that
matches nothing all produce:

```
### Number of sets Done:  0
### Number of scenes Done:  0
### Number of scenes failed:  0
```

and exit code 0 — indistinguishable, to any CI gate, from a full green run.

The filter makes this easy to hit by accident. It is applied as
`re.search(filter, values[0])` against the raw path field from the list file, and the help text
advertises `'^demo.*.scn$'` — an anchored pattern that will not match any entry written as
`subdir/demo_foo.scn`. There is no summary of how many scenes the filter removed unless
`--verbose` is on.

*Fix sketch:* validate that `--input` exists, and fail (or require an explicit
`--allow-empty`) when zero list files are found or zero scenes are selected. Report the
filtered-out count unconditionally.

**DECISION**: OK, (validation of existance of input) + (that any tests are found only if --allow-empt is not passed)


## C11. Keyframe matching uses a *relative* tolerance and can lock onto the wrong step

`tools/RegressionSceneData.py:341` (and `:466` for legacy)

```python
if frame_step < nbr_frames and np.isclose(simu_time, keyframes[frame_step]):
```

`np.isclose` defaults to `atol=1e-8, rtol=1e-5`, i.e. a tolerance of `1e-8 + 1e-5·|t|` that
**grows with simulated time**. Once that tolerance exceeds `dt`, several consecutive steps
satisfy the test and the *first* one wins:

| keyframe `t` | tolerance | width in steps at `dt = 1e-3` |
|---|---|---|
| 1.0 | 1.0e-05 | 0 |
| 10.0 | 1.0e-04 | 0.1 |
| 1 000.0 | 1.0e-02 | 10 |
| 10 000.0 | 1.0e-01 | 100 |

Beyond roughly `t > dt·1e5` the comparison is taken at the wrong simulation step, and because
`frame_step` advances on that early match, **every subsequent keyframe is compared at the
wrong time too**. Depending on the trajectory that produces either spurious failures (noise in
the suite) or, where the state changes slowly, a pass against a neighbouring frame that hides
a real timing regression.

This requires a long run (>100 000 steps at `dt = 1e-3`) to bite, so it is conditional — but
it is silent when it does, and long-running scenes are exactly where reference comparison is
most valuable.

*Fix sketch:* compare integer step indices instead of floats. The write side knows
`t = dt * step`, so storing the step number in the reference removes the float matching
entirely; failing that, use `abs(simu_time - keyframe) < dt/2` (an absolute, `dt`-scaled
tolerance).

**DECISION**: Your proposed condition is good : do it


## C12. JSON references carry no metadata, so staleness is undetectable

`tools/ReferenceFileIO.py:71-86`

`write_JSON_reference_file` writes a bare `{time: positions}` map. No format version, no
`dt`, no `steps`, no `dump_number_step`, no `epsilon`, no point count, no dof-per-point, no
object name, no scene path, no timestamp, no SOFA version.

This is the enabler for several findings above. Because nothing is recorded, the tool cannot
detect that a reference was written:

- with a different `steps` (C4) or a different `dump_number_step` (C7);
- for a different number of mechanical objects (C5);
- with a different `dt`, so the same keyframe times mean a different trajectory;
- by a different SOFA version, on a different platform, or by a different tool version.

The irony is that the CSV writer *does* emit a metadata header — `format_version`,
`dof_per_point`, `num_points`, `layout` (`ReferenceFileIO.py:52-63`) — and `regression_version`
exists precisely for that. But CSV is unreachable from the CLI (O5), so the only format ever
produced in practice is the one with no metadata at all.

*Fix sketch:* give the JSON format a header object alongside the frames (version, dt, steps,
dump_number_step, per-object names and shapes) and validate it on read; refuse a reference
whose recorded parameters differ from the ones requested, rather than comparing anyway.

**DECISION**: I like the proposed fix : do it


## C13. Duplicate scene entries corrupt references, silently and non-deterministically

`SofaRegressionProgram.py:70-80`; `tools/RegressionSceneData.py:234-243`

Nothing detects that two tasks target the same reference files. That happens whenever the same
scene path appears twice in a list file, or in two different `.regression-tests` files whose
reference directories overlap — easy, since `run_all_sets` deliberately merges the tasks of
**all** sets into one pool.

In sequential write mode the second write simply overwrites the first, so the reference
silently corresponds to only one of the two configurations; if the two lines specify different
`steps` or `dump_number_step`, the other line is then compared against a reference it does not
match (which C4 will not report).

In parallel write mode (`-j > 1`) the two writes run **concurrently against the same path**.
`write_references` writes directly to the final filename with no locking, no temp file, and no
atomic rename (`:234-243`), so the outcome is an interleaved, truncated or otherwise corrupt
gzip file. The failure surfaces much later, as an obscure decompression error during a compare
run, and it is not reproducible between runs.

The lack of atomic writes is a problem on its own: any interruption during
`--write-references` (Ctrl-C, OOM kill, CI timeout — and note there is no subprocess timeout,
O2) leaves a partially written reference file that looks present but is unreadable.

*Fix sketch:* detect duplicate reference targets while building the task pool and refuse to
run; write to a temporary file in the destination directory and `os.replace()` it into place.

**DECISION**: Let's rediscuss this one, I am not sure on the second solution you've proposed. I prefer to hold the logs in a string until we can simply block stram it into the file. This is still thread safe and will also reduce writing time.

## C14. Only positions are compared

`tools/RegressionSceneData.py:216`, `:343`; `tools/ReferenceFileIO.py:130-131`

The only quantity ever recorded or compared is `mechanical_object.position.value`. Velocities
are explicitly discarded even where the legacy format provides them
(`ReferenceFileIO.py:130-131`: `elif line.startswith("V="): continue`), and nothing else is
looked at: no forces, no rest positions, no constraint values or Lagrange multipliers, no
topology, no collision state, no energy, no solver iteration counts or residuals.

Whole classes of regression are therefore invisible by construction:

- a change that leaves positions within tolerance but corrupts velocities (integration scheme
  bugs, damping changes) — the next physical interaction differs, but the test passes;
- a solver that now needs 10× more iterations, or silently stops converging, while landing on
  a similar position — a severe performance and robustness regression, undetected (the
  `total_run_time` that *is* recorded is only printed, never asserted);
- topology or collision-detection changes that happen not to move the sampled dofs much.

This is arguably a scope decision rather than a defect, but it is a coverage limit worth
stating explicitly, because "the regression suite is green" is routinely read as a much
stronger claim than "sampled positions are within an accumulated tolerance".

**DECISION**: Don't change it. One added feature of this code rather then the legacy one is to add more timestamp to test, lreducing the possibility of such thing to happend. More over, position is the integral of the other states. There is very little chances that we get the same values after a thousand timestep if any of the derived states is wrong. Except maybe for cyclic behavior. We will keep this as is.

---

# Tier 2 — Other

Ordered by decreasing severity. None of these produce a false pass on their own, but several
degrade diagnosis badly enough to cause a real failure to be dismissed.

## O1. `--quiet` discards the reason for every failure

`SofaRegressionProgram.py:211-228`; `tools/RegressionHelper.py`

`--quiet` redirects **file descriptor 1** to `/dev/null` for the whole duration of the run, and
every helper in `RegressionHelper.py` prints to stdout — including `writeError`. All the
diagnostics emitted *during* the run are therefore destroyed: the worker error messages from
`apply_result` (`RegressionSceneList.py:228`, `:234`), every `writeError` from inside the
children (missing reference file, shape mismatch, timeline mismatch), and, in parallel mode,
the entire captured child output replayed by `_echo_captured_output` (which writes to
`sys.stdout`, still pointing at the redirected fd).

What survives is only what is printed after the fd is restored: the summary and the one-line
per-scene verdicts from `log_errors()`. So `--quiet` — the flag a CI job is most likely to
use — turns "scene X failed because its reference file is missing" into "scene X failed", with
the actual reason unrecoverable without a full re-run.

Sending `writeError`/`writeWarning` to stderr would fix this and would also let users separate
streams with ordinary shell redirection.

## O2. No timeout on a worker subprocess

`tools/RegressionWorker.py:110`

`subprocess.run(cmd, ...)` is called without `timeout=`. A scene that hangs — an infinite
solver loop, a deadlock, a modal dialog, a wait on missing input — hangs the parent forever. In
sequential mode the whole run stops; in parallel mode one pool slot is consumed permanently and
the run cannot complete. In CI this manifests as a job that burns its entire time budget and is
killed externally, with no indication of which scene was responsible.

`steps` is also unbounded above (`RegressionSceneList.py:82-90` only checks `steps > 0`), so a
typo like `steps 100000000` has the same effect without any hang at all.

## O3. Structural failures are logged as successes

`tools/RegressionSceneData.py:99-109`

Covered under C3 as it relates to the exit code; restated here as a reporting defect. Every
early `return False` in `compare_references` leaves `regression_failed == False`, so
`log_errors()` takes its `else` branch and prints `[Regression-Success]` for that scene. A run
can therefore print a green line for a scene it simultaneously counts in
`### Number of scenes failed`. Contradictory output is worse than no output, because it gives
a reader a reason to believe the failure is spurious.

## O4. Nothing pins the sources of run-to-run non-determinism

`tools/RegressionWorker.py:1-32`

The module docstring correctly identifies process-level SOFA state as a reproducibility hazard
and solves it thoroughly. But that is not the only source of non-determinism in a SOFA
simulation, and the others are not addressed:

- thread count. Nothing sets `OMP_NUM_THREADS` or any equivalent, so a multi-threaded solver
  or assembly can produce different floating-point summation orders on machines with different
  core counts — or between `-j 1` and `-j 8` on the same machine, where the children compete
  for cores. References written on one configuration may then not reproduce on another.
- oversubscription. `-j 0` spawns one child per logical core and each child may itself be
  multi-threaded, giving `cores²` threads.
- no RNG seeding and no floating-point environment control (FMA, denormal handling,
  `-ffast-math` builds).

Since the comparison is numerical with a tight default tolerance, these turn into flaky
failures whose cause is invisible from the output. Recording the environment in the reference
metadata (C12) and pinning the thread count would make it diagnosable.

## O5. CSV support is complete but unreachable; JSON metadata is the format actually used

`tools/RegressionWorker.py:55`, `:166`, `:206`, `:252`

`format` defaults to `"JSON"` at every level and no CLI flag exposes it; no call site anywhere
passes `"CSV"`. So the CSV reader and writer, the metadata header, `regression_version`, and
the size and timeline validation that exist **only** in the CSV branch of
`compare_references` (`:288-295`, `:306-311`) are all dead code in practice. The JSON branch
(`:313-320`) has no equivalent validation: it does not even check that the secondary mechanical
objects share the primary's timeline.

The result is that the better-validated format is the one that cannot be selected.

## O6. JSON keyframe keys rely on Python's float `repr` round-tripping

`tools/RegressionSceneData.py:223`, `:348`; `tools/ReferenceFileIO.py:78-86`

Frames are stored with a Python `float` as the dict key (`numpy_data[meca_id][t] = ...`), which
JSON serialises via `repr`. On read, `read_JSON_reference_file` returns the dict still keyed by
those **strings** plus a parallel list of `float`s, and the lookup is
`numpy_data[meca_id][str(keyframes[frame_step])]`.

This works today only because CPython 3 guarantees `str(float)` is the shortest round-tripping
representation and `str is repr` for floats. It is a hidden coupling to an implementation
detail: any reference file produced by another language, another JSON writer, or a Python
version with different float formatting will raise `KeyError` on a key whose numeric value is
correct. The `KeyError` would also be uncaught — the `try` only wraps the loading phase
(`:274-329`), not the comparison loop — so it surfaces as a worker crash rather than a clear
message.

Storing integer step indices (see C11) removes the problem.

## O7. Multi-parent nodes are collected twice

`tools/RegressionSceneData.py:130-141`

`parse_node` recurses over `node.children` from the root with no visited-set. A SOFA node can
have several parents, so any node in a diamond is reached once per path and its mechanical
state is appended to `meca_objs` more than once. That produces duplicate reference files under
different indices, doubles the work for those objects, and — because indices are positional
(C5) — shifts the index of everything that follows.

It is consistent between the write and compare passes, so it does not cause a false pass on
its own; it wastes time and makes the reference set harder to reason about.

Relatedly, `is_simulated` (`:12-22`) recurses over `node.parents` with no memoisation, so on a
wide DAG the ancestor search can be re-walked many times.

## O8. `--replay` crashes on the reference edges

`tools/RegressionSceneData.py:34-48`

`ReplayState` is unguarded at both ends:

- `if (self.keyframes[0] == 0.0)` raises `IndexError` on a reference with no frames;
- `onAnimateEndEvent` indexes `self.keyframes[self.frame_step]` with no bounds check, so once
  the last keyframe has been consumed the next animation step raises `IndexError` — i.e.
  replaying past the end of the reference always throws.

`ReplayState` also reads JSON exclusively (`:34`) and `add_compare_state` hardcodes `.json.gz`
(`:149`), so replay does not work for CSV or legacy references. Given O5 that is currently
moot, but it is a second place where format support diverges.

Replay is also the one path that loads a scene **in the parent process** (`:290-292`), so it is
exempt from the isolation guarantee. That is defensible for an interactive tool, but it means
replay results are not necessarily the results the batch run produced.

## O9. The "parent never imports SOFA" invariant is documented but not upheld

`SofaRegressionProgram.py:13-14`; `tools/RegressionSceneData.py:7`

`RegressionWorker.py:29-32` states that only the standard library is imported at module level
"so that importing this module in the parent does NOT import SOFA (the parent must never load
or simulate a scene, otherwise the isolation would be defeated)". `RegressionWorker` honours
this scrupulously.

The parent nonetheless imports SOFA, twice over: directly at `SofaRegressionProgram.py:13-14`,
and transitively because `RegressionSceneList` imports `RegressionSceneData`, which does
`import Sofa` at module level. What is actually preserved is the weaker (and sufficient)
property that the parent never *loads or simulates* a scene — except on the `--replay` path,
where it does both.

The invariant as written is therefore misleading to a future maintainer, who may reasonably
conclude that the parent is SOFA-free and that touching Sofa in parent code is what would
break isolation, when in fact the line to hold is `Sofa.Simulation.load`.

## O10. Write-mode sampling does not produce `dump_number_step` keyframes

`tools/RegressionSceneData.py:196`, `:212-229`

```python
modulo_step = self.steps / self.dump_number_step      # a float
...
if step == 0 or counter_step >= modulo_step or step == self.steps:
```

The parameter reads as "number of frames to dump", but the sampling condition produces neither
that count nor evenly spaced keyframes: `modulo_step` is a float compared against an integer
counter, `step == 0` and `step == self.steps` force extra samples at both ends (the final one
possibly right after a periodic sample), and the counter resets only on a sample. So the number
of recorded frames is `dump_number_step` plus one or two, with a ragged last interval.

Two edge cases: `dump_number_step > steps` makes `modulo_step < 1`, so **every** step is
recorded — a reference file thousands of times larger than intended, with only
`dump_number_step > 0` validated to prevent it; and `dump_number_step == steps` records every
step by design. Neither is rejected or warned about.

The name is also misleading in the sources themselves: the docstring at `:74` declares it
`bool m_dumpNumberStep`, which it is not.

## O11. Inconsistent and duplicated defaults for `meca_in_mapping`

`tools/RegressionSceneList.py:74` vs `tools/RegressionSceneData.py:61`

The list-file parser defaults `meca_in_mapping` to `False`; the `RegressionSceneData`
constructor defaults the same parameter to `True`. The worker always passes it explicitly, so
there is no live bug — but the two defaults disagree about a setting that directly controls how
many mechanical objects get tested, and a future call site that omits the argument will get the
opposite of the documented CLI behaviour.

The same duplication applies to `steps`, `epsilon` and `dump_number_step`, whose defaults are
written out in both files.

## O12. Legacy mode is only half-wired

`SofaRegressionProgram.py:198-200`; `tools/RegressionSceneData.py:402-527`

- `--legacy-regression` combined with `--write-references` is silently ignored: `legacy` is
  never consulted in the write path (`RegressionWorker.py:295-297`), so the run writes
  **new-format** `.json.gz` references while the user believes they asked for legacy ones.
- `compare_legacy_references` never accumulates `total_run_time`, so its verdict line always
  reports `run time: 0.0 seconds`.
- its frame-count warning compares frames against *steps* (`:456`), which are different
  quantities as soon as `dump_number_step > 1`, so the warning fires spuriously.
- the JSON filenames computed by `load_scene` (`:180-187`) are unused in legacy mode, which
  builds its own paths inline (`:422`) — two naming schemes with no shared helper.

## O13. `read_legacy_reference` raises the wrong error on a malformed file

`tools/ReferenceFileIO.py:101-116`

`current_time` is never initialised before the loop, yet the `X=` branch tests
`if current_time is None` to produce a clear `"X found before T"` message. If a file really
does start with `X=`, the check itself raises `UnboundLocalError` and the intended diagnostic
is unreachable. `ref_data = []` (`:92`) is also built and never used.

## O14. Dead code and unused parameters

Verified by grep — each of these appears only at its own definition:

| Symbol | Location | Note |
|---|---|---|
| `args.output` / `--output` | `SofaRegressionProgram.py:118` | declared and documented, never read anywhere |
| `write_sets_references` | `SofaRegressionProgram.py:82` | single-set entry point, unreachable from the CLI |
| `compare_sets_references` | `SofaRegressionProgram.py:90` | idem |
| `RegressionSceneList.write_references` | `:253` | builds a task descriptor then ignores it when calling `run_scene_in_subprocess`; also drops `legacy` |
| `RegressionSceneList.compare_references` | `:269` | unused |
| `print_log` parameter | `:253` | never read |
| `set_legacy_mode` | `:42` | both callers assign the attribute directly |
| `add_write_state` | `RegressionSceneData.py:157` | uses the legacy filename scheme, inconsistent with `load_scene` |
| `print_meca_objs` | `:121` | idem |
| `self.mins` / `self.maxs` | `:84-85` | initialised, never used |

A `--output` flag that is advertised in `--help` and does nothing is the worst of these: it
invites users to pass it and assume it took effect.

## O15. Smaller robustness and hygiene issues

- **`load_scene` has no `else` for an unknown format** (`RegressionSceneData.py:182-185`), so
  `_filename` is left unbound (or stale from the previous iteration). The write and compare
  methods do validate the format; `load_scene` does not.
- **Missing f-string prefix**: `RegressionSceneData.py:171` prints the literal
  `While trying to load {self.file_scene_path}`. The accompanying `raise RuntimeError` carries
  no message either, so the parent reports `While trying to compare <scene>: ` with an empty
  reason — one of the least diagnosable failures the tool can emit.
- **`old_fd = os.dup(1)` is executed unconditionally** (`SofaRegressionProgram.py:211`) but
  closed only inside the `--quiet` branch (`:228`): a leaked descriptor on every normal run.
- **`--replay` always exits 0** (`:209`), including when the index is out of range and the only
  output is an error message.
- **`SOFA_ROOT` is checked at import time** (`:6-11`), so `--help` does not work without a
  SOFA installation. The parent also builds the path by string concatenation with `/`
  (`:10`) while the child uses `os.path.join` (`RegressionWorker.py:269`) — the parent is
  not portable to Windows.
- **No machine-readable output.** Results exist only as coloured stdout text; there is no
  JUnit/JSON report, so a CI system can surface a pass/fail bit but cannot show which scene
  failed, its error magnitude, or a history. All the numbers needed for one already cross the
  process boundary as JSON.
- **`total_error` is computed, reported, and never thresholded** — only `error_by_dof` gates
  the verdict, which makes the two-number error line ambiguous about what actually failed.
- **Report ordering is non-deterministic**: `os.walk` order sets the set order, and in parallel
  mode results are emitted in completion order, so two runs of the same suite produce logs
  that cannot be diffed.
- **Temporary result files leak** if the parent is killed between `mkstemp`
  (`RegressionWorker.py:82`) and `_safe_remove` (`:121`).
- **Scene paths cannot contain whitespace**, since list-file lines are parsed with
  `line.split()` (`RegressionSceneList.py:158`).
- **Extra fields on a scene line are only warned about** (`:69-70`), so a trailing typo or a
  sixth column added by a future format is silently ignored.
- **Copy-pasted C++ declarations serve as docstrings** in `RegressionSceneData.__init__`
  (`:62-75`) and `RegressionSceneList.__init__` (`:12-15`), documenting types and names that do
  not match the Python code (`bool m_dumpNumberStep` for an `int`, `m_fileScenePath` for
  `file_scene_path`).
- **`write_CSV_reference_file` mixes a `csv.writer` with raw `f.write` calls** (`:50-65`),
  constructing the writer before the header is written — fragile if anyone adds buffering.
- **The CSV `layout` metadata strings are wrong** (`:56-63`): `time,X0,Y1,...` mixes index 0
  and 1 for the first point, and the rigid (7-dof) layout reads `...,QzN1,QwN`. They are
  comments, but they are also the only documentation of the on-disk layout.
- **No `format_version` check on read** (`read_CSV_reference_file`, `:19-44`): the field is
  written but never validated, so a future format change would be misparsed rather than
  rejected.
- **The module has no tests of its own.** There is no test suite covering the list-file parser,
  the error metric, the worker protocol or the reference IO — the tool that gates SOFA against
  regressions has no regression tests itself. Most of the Tier 1 findings above (C1, C2, C4,
  C8) are reachable by a unit test with a handful of synthetic reference files and no SOFA at
  all.

---

# Suggested order of work

If the Tier 1 list is to be addressed incrementally, this order maximises safety gained per
change and keeps each step independently reviewable:

1. **C1, C2, C8** — three guards in `compare_references` plus one in `parse_scene_line`
   (non-finite error, `nbr_meca == 0`, non-finite epsilon). A few lines each, no format change,
   no reference regeneration. These close the outright false passes.
2. **C3** — make `nbr_tested_frame == 0` count as a failure and set a failure flag on every
   early `return False`. Aligns the exit code and the per-scene log.
3. **C10, C9** — validate `--input`, fail on zero selected scenes, and make write failures
   non-zero-exit. Closes the "green run that tested nothing" hole.
4. **C4** — fail when `nbr_tested_frame != nbr_frames`. Also catches most of C5 and C12 in
   practice.
5. **C12** — add a metadata header to the JSON format and validate it on read. This is the
   format change; doing it after step 4 means the cheap checks are already in place if
   regeneration is needed.
6. **C11, C13** — integer step indices in the reference (subsumes C11 and O6), duplicate-target
   detection, atomic writes.
7. **C6, C7** — the error-metric redesign. Deliberately last: it is the only change that
   invalidates every `epsilon` in every list file and therefore needs a migration plan rather
   than a patch.

C14 (positions only) is a scope decision for the maintainers rather than a fix.
