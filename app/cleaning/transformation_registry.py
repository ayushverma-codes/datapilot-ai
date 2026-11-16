from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from app.models.contracts import PipelineError
from app.profiling.type_inference import date_candidates, numeric


class Params(BaseModel):
    model_config = ConfigDict(extra='forbid')


class StringParams(Params):
    mode: Literal['lower', 'upper', 'casefold']


class MappingParams(Params):
    mapping: dict[str, str] = Field(max_length=100)


class FillParams(Params):
    value: str = Field(max_length=1000)


class DateParams(Params):
    format: Literal['%Y-%m-%d', '%Y/%m/%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%m-%d-%Y', 'unambiguous'] = 'unambiguous'


@dataclass(frozen=True)
class Transformation:
    function: object
    parameters: type[BaseModel]
    risk: str
    column_required: bool = True
    removes_rows: bool = False


REGISTRY = {}


def register(name, parameters=Params, risk='medium', column_required=True, removes_rows=False):
    def decorator(function):
        REGISTRY[name] = Transformation(function, parameters, risk, column_required, removes_rows)
        return function
    return decorator


@register('trim_whitespace', risk='low')
def trim(frame, columns, params):
    for c in columns:
        frame[c] = frame[c].str.strip().replace('', pd.NA)
    return frame


@register('normalize_strings', StringParams)
def normalize(frame, columns, params):
    for c in columns:
        frame[c] = getattr(frame[c].str, params.mode)()
    return frame


@register('remove_empty_rows', risk='low', column_required=False, removes_rows=True)
def empty_rows(frame, columns, params):
    return frame.dropna(how='all')


@register('remove_exact_duplicates', risk='low', column_required=False, removes_rows=True)
def duplicates(frame, columns, params):
    return frame.drop_duplicates(keep='first')


@register('convert_numeric')
def convert_numeric(frame, columns, params):
    def convert(value):
        if pd.isna(value):
            return value
        if numeric(value) is None:
            raise ValueError('Column contains invalid or non-finite numerical values')
        number = Decimal(str(value).strip())
        # CSV stores textual values; decimal avoids float rounding or scientific notation loss.
        return format(number, 'f').rstrip('0').rstrip('.') if '.' in format(number, 'f') else format(number, 'f')
    for c in columns:
        frame[c] = frame[c].map(convert).astype('string')
    return frame


@register('parse_dates', DateParams)
def dates(frame, columns, params):
    def convert(value):
        if pd.isna(value):
            return value
        value = str(value).strip()
        # Canonical output accepted on repeated execution.
        if 'T' in value:
            try:
                return datetime.fromisoformat(value).isoformat()
            except ValueError:
                pass
        if params.format != 'unambiguous':
            return datetime.strptime(value, params.format).isoformat()
        candidates = {date for date, _ in date_candidates(value)}
        if len(candidates) != 1:
            raise ValueError('Ambiguous or invalid date requires an explicit format')
        return candidates.pop()
    for c in columns:
        frame[c] = frame[c].map(convert).astype('string')
    return frame


@register('category_normalization', MappingParams)
def categories(frame, columns, params):
    for c in columns:
        frame[c] = frame[c].replace(params.mapping)
    return frame


@register('fill_missing', FillParams)
def fill(frame, columns, params):
    for c in columns:
        frame[c] = frame[c].fillna(params.value)
    return frame


@register('flag_outliers', risk='low')
def flag(frame, columns, params):
    # Flags are audit metadata, never deletions or changes to data columns.
    return frame


def validate_operation(operation, columns, parameters, available):
    if operation not in REGISTRY:
        raise PipelineError('UNKNOWN_OPERATION', 'Operation is not allowed')
    spec = REGISTRY[operation]
    if len(set(columns)) != len(columns) or any(c not in available for c in columns):
        raise PipelineError('INVALID_COLUMNS', 'Operation refers to unknown or duplicate columns')
    if spec.column_required != bool(columns):
        raise PipelineError('INVALID_COLUMNS', 'Operation has an invalid column scope')
    try:
        params = spec.parameters.model_validate(parameters)
    except ValueError as exc:
        raise PipelineError('INVALID_PARAMETERS', 'Operation parameters are invalid') from exc
    if operation == 'category_normalization':
        if any(target in params.mapping and params.mapping[target] != target for target in params.mapping.values()):
            raise PipelineError('NON_IDEMPOTENT_MAPPING', 'Category mapping contains chains or cycles')
    return spec, params
