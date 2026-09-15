"""Small bounded JSON Schema 2020-12 subset used by BlendRelay tool inputs."""
import math


class SchemaError(ValueError):
    pass


def validate(value, schema, path='arguments', depth=0):
    if depth > 16:
        raise SchemaError(f'{path} exceeds the schema depth limit')
    if 'enum' in schema and value not in schema['enum']:
        raise SchemaError(f'{path} must be one of {schema["enum"]}')
    kind = schema.get('type')
    valid = {
        'object': lambda v: isinstance(v, dict),
        'array': lambda v: isinstance(v, list),
        'string': lambda v: isinstance(v, str),
        'integer': lambda v: isinstance(v, int) and not isinstance(v, bool),
        'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v),
        'boolean': lambda v: isinstance(v, bool),
        'null': lambda v: v is None,
    }
    if kind and (kind not in valid or not valid[kind](value)):
        raise SchemaError(f'{path} must be {kind}')
    if kind == 'object':
        properties = schema.get('properties', {})
        missing = set(schema.get('required', ())) - set(value)
        if missing:
            raise SchemaError(f'{path} is missing: {", ".join(sorted(missing))}')
        if schema.get('additionalProperties') is False:
            extra = set(value) - set(properties)
            if extra:
                raise SchemaError(f'{path} has unknown fields: {", ".join(sorted(extra))}')
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], f'{path}.{key}', depth + 1)
    elif kind == 'array':
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 1000000):
            raise SchemaError(f'{path} has an invalid number of items')
        for index, item in enumerate(value):
            validate(item, schema.get('items', {}), f'{path}[{index}]', depth + 1)
    elif kind == 'string':
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 1000000):
            raise SchemaError(f'{path} has an invalid length')
    elif kind in ('integer', 'number'):
        if value < schema.get('minimum', value) or value > schema.get('maximum', value):
            raise SchemaError(f'{path} is outside the allowed range')
    return value
