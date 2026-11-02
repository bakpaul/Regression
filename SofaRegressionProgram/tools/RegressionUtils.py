# >>> CI-UPDATE-REF BEGIN
"""Utilities shared by the regression scripts and the CI scripts.

This module must stay free of any SOFA import: the CI scripts use it outside of
a SOFA environment (e.g. to compute the scenes filter, or to delete references).
"""
import json
import os
import re
from pathlib import Path

REGRESSION_FILE_EXTENSION = ".regression-tests"
STATE_PREFIX = "RegressionStateScenes"
TOPOLOGY_PREFIX = "RegressionTopologyScenes"
REGRESSION_TYPE_PREFIXES = {"STATE": STATE_PREFIX, "TOPOLOGY": TOPOLOGY_PREFIX}


def find_regression_files(input_folders, reg_type='ALL'):
    """Yield the paths of the regression test files found under `input_folders`.

    Args:
        input_folders (list(str)): folders to walk recursively.
        reg_type (str): 'ALL' or a key of REGRESSION_TYPE_PREFIXES.

    Yields:
        (path, selected): `selected` is False for a regression file skipped because of `reg_type`.
    """
    for directory in input_folders:
        for root, dirs, files in os.walk(directory):
            for file in files:
                if not file.endswith(REGRESSION_FILE_EXTENSION):
                    continue
                selected = reg_type == 'ALL' or REGRESSION_TYPE_PREFIXES[reg_type] in file
                yield os.path.join(root, file), selected


def resolve_reference_dir(regression_file_path, first_value, regression_dir=None):
    """Return the reference directory declared by the first line of a regression file.

    Args:
        regression_file_path (str): the regression file (relative paths are relative to its folder).
        first_value (str): first non-comment field of the file.
        regression_dir (str): value for $REGRESSION_DIR, default is the environment variable.

    Returns:
        str, or None if $REGRESSION_DIR is required but not defined.
    """
    if "$REGRESSION_DIR" in first_value:
        regression_dir = regression_dir or os.environ.get("REGRESSION_DIR")
        if regression_dir is None:
            return None
        return first_value.replace("$REGRESSION_DIR", regression_dir)
    return os.path.abspath(os.path.join(os.path.dirname(regression_file_path), first_value))


def reference_file_path(reference_dir, os_name, scene_value):
    """Reference path prefix of a scene (the reference files are `<prefix>.reference_*`)."""
    return os.path.normpath(os.path.join(reference_dir, os_name, scene_value))


def list_reference_files(reference_prefix):
    """Existing reference files of a scene: `<prefix>.reference*` files of the reference folder."""
    folder, name = os.path.split(reference_prefix)
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, f) for f in os.listdir(folder) if f.startswith(name + ".reference")]


def read_regression_file(regression_file_path, regression_dir=None):
    """Return (reference_dir, [scene path as written in the file, ...]) of a regression file."""
    reference_dir = None
    scenes = []
    with open(regression_file_path, 'r') as the_file:
        for line in the_file.readlines():
            if line.startswith("#"):
                continue
            values = line.split()
            if len(values) == 0:
                continue
            if reference_dir is None:
                reference_dir = resolve_reference_dir(regression_file_path, values[0], regression_dir)
                if reference_dir is None:
                    return None, []
                continue
            scenes.append(values[0])
    return reference_dir, scenes


def find_scene_entries(input_folders, scene_path, reg_type='ALL', regression_dir=None):
    """Locate a scene in the regression files.

    Args:
        scene_path (str): absolute path of the scene.

    Returns:
        list of (regression_file, scene_value, reference_dir) where `scene_value` is the scene path
        as written in the regression file (relative to its folder), i.e. what `--filter` matches.
    """
    scene_path = os.path.normpath(scene_path)
    found = []
    for file_path, selected in find_regression_files(input_folders, reg_type):
        if not selected:
            continue
        reference_dir, scenes = read_regression_file(file_path, regression_dir)
        if reference_dir is None:
            continue
        for value in scenes:
            if os.path.normpath(os.path.join(os.path.dirname(file_path), value)) == scene_path:
                found.append((file_path, value, reference_dir))
    return found


def build_filter_regex(scene_values):
    """Regex for `--filter` selecting exactly the given scenes.

    Args:
        scene_values (list(str)): scene paths as written in the regression files
            (see find_scene_entries), '/' separated.

    The filter is applied with re.search on the scene path: every alternative is anchored and escaped.
    """
    values = sorted({v.replace("\\", "/") for v in scene_values})
    if not values:
        return None
    return "^(?:" + "|".join(re.escape(v) for v in values) + ")$"


def load_update_ref(update_ref):
    """Parse a `ci-update-ref` structure (JSON string or dict): {"sofa": [scene, ...], "plugin": [...]}."""
    data = json.loads(update_ref) if isinstance(update_ref, str) else update_ref
    if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, list) and all(isinstance(s, str) for s in v)
            for k, v in data.items()):
        raise ValueError("ci-update-ref must be an object {repo: [scene paths]}")
    return data


def build_filter_from_update_ref(update_ref, repo_roots, input_folders, reg_type='ALL', regression_dir=None):
    """Regex for `--filter` from a `ci-update-ref` structure.

    Args:
        update_ref: the `ci-update-ref` JSON (string or dict). Scene paths are relative to the repo root.
        repo_roots (dict): repo key ("sofa" or plugin name) -> root directory.
        input_folders (list(str)): the `--input` folders given to the regression program.

    Raises:
        ValueError if a listed scene is not declared in any regression file.
    """
    values = []
    for repo, scenes in load_update_ref(update_ref).items():
        if repo not in repo_roots:
            raise ValueError(f"Unknown repository '{repo}' in ci-update-ref")
        for scene in scenes:
            entries = find_scene_entries(input_folders, os.path.join(repo_roots[repo], scene), reg_type, regression_dir)
            if not entries:
                raise ValueError(f"Scene '{scene}' of '{repo}' is not listed in any regression file")
            values.extend(value for _, value, _ in entries)
    return build_filter_regex(values)


def failed_scenes_to_update_ref(failed_scene_paths, repo_roots):
    """Build a `ci-update-ref` structure from absolute scene paths.

    Longest matching root wins (a plugin under the SOFA tree is not attributed to "sofa").
    """
    result = {}
    roots = sorted(((k, os.path.normpath(v)) for k, v in repo_roots.items()), key=lambda kv: -len(kv[1]))
    for scene in failed_scene_paths:
        scene = os.path.normpath(scene)
        for key, root in roots:
            if scene.startswith(root + os.sep):
                result.setdefault(key, []).append(Path(os.path.relpath(scene, root)).as_posix())
                break
    return {k: sorted(set(v)) for k, v in result.items()}


def guess_repo_roots(scene_path, input_folders):
    """Return (key, root) of the repository owning a scene, following the `ci-update-ref` conventions.

    - scene in a plugin of `<src>/applications/plugins|projects/<name>`: ("<name>", that folder)
    - scene in `<src>/examples` or elsewhere in `<src>/applications`: ("sofa", `<src>`)
    - scene in a folder of fetched plugins (any other input folder): ("<name>", `<folder>/<name>`)
    Returns (None, None) if the scene is not under any input folder.
    """
    scene_path = os.path.abspath(scene_path)
    for folder in input_folders:
        folder = os.path.abspath(folder)
        if not scene_path.startswith(folder + os.sep):
            continue
        parts = Path(os.path.relpath(scene_path, folder)).parts
        if os.path.basename(folder) in ("examples", "applications"):
            src = os.path.dirname(folder)
            if os.path.basename(folder) == "applications" and len(parts) > 2 and parts[0] in ("plugins", "projects"):
                return parts[1], os.path.join(folder, parts[0], parts[1])
            return "sofa", src
        return parts[0], os.path.join(folder, parts[0])
    return None, None


def failed_scenes_to_update_ref_auto(failed_scene_paths, input_folders):
    """Build a `ci-update-ref` structure from absolute failed scene paths, guessing the repositories."""
    result = {}
    for scene in failed_scene_paths:
        key, root = guess_repo_roots(scene, input_folders)
        if key is not None:
            result.setdefault(key, []).append(Path(os.path.relpath(os.path.abspath(scene), root)).as_posix())
    return {k: sorted(set(v)) for k, v in result.items()}
# <<< CI-UPDATE-REF END
