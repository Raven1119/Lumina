"""Validated organ configuration; model identity still belongs to model_policy."""
from copy import deepcopy
from pathlib import Path
import tomllib
import math

ROOT = Path(__file__).resolve().parents[1]

def load_config(path=None, overrides=None):
    with (ROOT/'config/lumina.toml').open('rb') as stream:
        config = tomllib.load(stream)
    for supplied in ([tomllib.loads(Path(path).read_text())] if path else []) + ([overrides] if overrides else []):
        for section, values in supplied.items():
            if section not in config or not isinstance(values, dict) or set(values)-set(config[section]):
                raise ValueError('unknown_lumina_config')
            config[section].update(deepcopy(values))
    if config['mind']['protocol'] not in ('a1', 'a2'):
        raise ValueError('invalid_mind_protocol')
    if config['language']['render'] not in ('always','mind_choice','proactive_only','never'):
        raise ValueError('invalid_language_render')
    if config['mind']['nondialogue_thinking'] != 'low':
        raise ValueError('invalid_nondialogue_thinking')
    for section, values in config.items():
        for name, value in values.items():
            if name in ('db_path','path','protocol','model','render','nondialogue_thinking'):
                if not isinstance(value,str) or (not value and name!='model'):
                    raise ValueError('invalid_lumina_setting')
            elif type(value) not in (int,float) or not math.isfinite(value) or (value < 0 if name == 'nondialogue_recent_turns' else value <= 0):
                raise ValueError('invalid_lumina_number')
            elif name != 'first_reply_timeout_s' and type(value) is not int:
                raise ValueError('integer_lumina_setting_required')
    return config
