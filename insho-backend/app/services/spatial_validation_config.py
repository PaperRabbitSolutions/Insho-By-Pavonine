"""Validation policy stored in the existing project and scene JSON fields."""

import hashlib
import json

from fastapi import HTTPException
from pydantic import ValidationError

from app.schemas.spatial_validation import CONFIG_KEY, CollisionOptions, ValidationConfig


def stored_config(container, field):
    if not isinstance(container, dict) or CONFIG_KEY not in container:
        return ValidationConfig()
    try:
        return ValidationConfig.model_validate(container[CONFIG_KEY])
    except ValidationError as exc:
        details = [
            {"loc": [field, CONFIG_KEY, *error["loc"]], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors(include_url=False, include_context=False, include_input=False)
        ]
        raise HTTPException(422, detail=details) from exc


def effective_config(project, scene, overrides):
    config = ValidationConfig(
        collision=CollisionOptions(include_hidden=False, ignore_ancestor_pairs=True, report_touching=False),
        require_zone_assignment=False,
    ).model_dump(exclude_none=True)
    for layer in (stored_config(project.constraints, "constraints"), stored_config(scene.metadata_json, "metadata"), overrides):
        values = layer.model_dump(exclude_none=True)
        for rule, options in values.pop("rules").items():
            config["rules"].setdefault(rule, {}).update(options)
        config["collision"].update(values.pop("collision"))
        config.update(values)
    return ValidationConfig.model_validate(config)


def fingerprint(config):
    payload = config.model_dump(mode="json", exclude_none=True)
    for container, key in ((payload, "exclusive_zone_pairs"), (payload["collision"], "allowed_pairs")):
        if key in container:
            container[key] = sorted({tuple(sorted(pair.values())) for pair in container[key]})
    if "clearances" in payload:
        payload["clearances"].sort(key=lambda item: item["id"])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
