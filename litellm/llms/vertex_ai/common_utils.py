import re
from typing import Dict, List, Literal, Optional, Tuple, Union

import httpx

from litellm import supports_response_schema, supports_system_messages, verbose_logger
from litellm.llms.base_llm.chat.transformation import BaseLLMException
from litellm.types.llms.vertex_ai import PartType


class VertexAIError(BaseLLMException):
    def __init__(
        self,
        status_code: int,
        message: str,
        headers: Optional[Union[Dict, httpx.Headers]] = None,
    ):
        super().__init__(message=message, status_code=status_code, headers=headers)


def get_supports_system_message(
    model: str, custom_llm_provider: Literal["vertex_ai", "vertex_ai_beta", "gemini"]
) -> bool:
    try:
        _custom_llm_provider = custom_llm_provider
        if custom_llm_provider == "vertex_ai_beta":
            _custom_llm_provider = "vertex_ai"
        supports_system_message = supports_system_messages(
            model=model, custom_llm_provider=_custom_llm_provider
        )
    except Exception as e:
        verbose_logger.warning(
            "Unable to identify if system message supported. Defaulting to 'False'. Received error message - {}\nAdd it here - https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json".format(
                str(e)
            )
        )
        supports_system_message = False

    return supports_system_message


def get_supports_response_schema(
    model: str, custom_llm_provider: Literal["vertex_ai", "vertex_ai_beta", "gemini"]
) -> bool:
    _custom_llm_provider = custom_llm_provider
    if custom_llm_provider == "vertex_ai_beta":
        _custom_llm_provider = "vertex_ai"

    _supports_response_schema = supports_response_schema(
        model=model, custom_llm_provider=_custom_llm_provider
    )

    return _supports_response_schema


from typing import Literal, Optional

all_gemini_url_modes = Literal["chat", "embedding", "batch_embedding"]


def _get_vertex_url(
    mode: all_gemini_url_modes,
    model: str,
    stream: Optional[bool],
    vertex_project: Optional[str],
    vertex_location: Optional[str],
    vertex_api_version: Literal["v1", "v1beta1"],
) -> Tuple[str, str]:
    url: Optional[str] = None
    endpoint: Optional[str] = None
    if mode == "chat":
        ### SET RUNTIME ENDPOINT ###
        endpoint = "generateContent"
        if stream is True:
            endpoint = "streamGenerateContent"
            url = f"https://{vertex_location}-aiplatform.googleapis.com/{vertex_api_version}/projects/{vertex_project}/locations/{vertex_location}/publishers/google/models/{model}:{endpoint}?alt=sse"
        else:
            url = f"https://{vertex_location}-aiplatform.googleapis.com/{vertex_api_version}/projects/{vertex_project}/locations/{vertex_location}/publishers/google/models/{model}:{endpoint}"

        # if model is only numeric chars then it's a fine tuned gemini model
        # model = 4965075652664360960
        # send to this url: url = f"https://{vertex_location}-aiplatform.googleapis.com/{version}/projects/{vertex_project}/locations/{vertex_location}/endpoints/{model}:{endpoint}"
        if model.isdigit():
            # It's a fine-tuned Gemini model
            url = f"https://{vertex_location}-aiplatform.googleapis.com/{vertex_api_version}/projects/{vertex_project}/locations/{vertex_location}/endpoints/{model}:{endpoint}"
            if stream is True:
                url += "?alt=sse"
    elif mode == "embedding":
        endpoint = "predict"
        url = f"https://{vertex_location}-aiplatform.googleapis.com/v1/projects/{vertex_project}/locations/{vertex_location}/publishers/google/models/{model}:{endpoint}"
        if model.isdigit():
            # https://us-central1-aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/us-central1/endpoints/$ENDPOINT_ID:predict
            url = f"https://{vertex_location}-aiplatform.googleapis.com/{vertex_api_version}/projects/{vertex_project}/locations/{vertex_location}/endpoints/{model}:{endpoint}"

    if not url or not endpoint:
        raise ValueError(f"Unable to get vertex url/endpoint for mode: {mode}")
    return url, endpoint


def _get_gemini_url(
    mode: all_gemini_url_modes,
    model: str,
    stream: Optional[bool],
    gemini_api_key: Optional[str],
) -> Tuple[str, str]:
    _gemini_model_name = "models/{}".format(model)
    if mode == "chat":
        endpoint = "generateContent"
        if stream is True:
            endpoint = "streamGenerateContent"
            url = "https://generativelanguage.googleapis.com/v1beta/{}:{}?key={}&alt=sse".format(
                _gemini_model_name, endpoint, gemini_api_key
            )
        else:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/{}:{}?key={}".format(
                    _gemini_model_name, endpoint, gemini_api_key
                )
            )
    elif mode == "embedding":
        endpoint = "embedContent"
        url = "https://generativelanguage.googleapis.com/v1beta/{}:{}?key={}".format(
            _gemini_model_name, endpoint, gemini_api_key
        )
    elif mode == "batch_embedding":
        endpoint = "batchEmbedContents"
        url = "https://generativelanguage.googleapis.com/v1beta/{}:{}?key={}".format(
            _gemini_model_name, endpoint, gemini_api_key
        )

    return url, endpoint


def _check_text_in_content(parts: List[PartType]) -> bool:
    """
    check that user_content has 'text' parameter.
        - Known Vertex Error: Unable to submit request because it must have a text parameter.
        - 'text' param needs to be len > 0
        - Relevant Issue: https://github.com/BerriAI/litellm/issues/5515
    """
    has_text_param = False
    for part in parts:
        if "text" in part and part.get("text"):
            has_text_param = True

    return has_text_param


def _build_vertex_schema(parameters: dict):
    """
    This is a modified version of https://github.com/google-gemini/generative-ai-python/blob/8f77cc6ac99937cd3a81299ecf79608b91b06bbb/google/generativeai/types/content_types.py#L419
    """
    defs = parameters.pop("$defs", {})
    # flatten the defs
    for name, value in defs.items():
        unpack_defs(value, defs)
    unpack_defs(parameters, defs)

    # 5. Nullable fields:
    #     * https://github.com/pydantic/pydantic/issues/1270
    #     * https://stackoverflow.com/a/58841311
    #     * https://github.com/pydantic/pydantic/discussions/4872
    convert_to_nullable(parameters)
    add_object_type(parameters)
    # Postprocessing
    # 4. Suppress unnecessary title generation:
    #    * https://github.com/pydantic/pydantic/issues/1051
    #    * http://cl/586221780
    strip_field(parameters, field_name="title")

    strip_field(
        parameters, field_name="$schema"
    )  # 5. Remove $schema - json schema value, not supported by OpenAPI - causes vertex errors.
    strip_field(
        parameters, field_name="$id"
    )  # 6. Remove id - json schema value, not supported by OpenAPI - causes vertex errors.

    return parameters


def unpack_defs(schema, defs):
    properties = schema.get("properties", None)
    if properties is None:
        return

    for name, value in properties.items():
        ref_key = value.get("$ref", None)
        if ref_key is not None:
            ref = defs[ref_key.split("defs/")[-1]]
            unpack_defs(ref, defs)
            properties[name] = ref
            continue

        anyof = value.get("anyOf", None)
        if anyof is not None:
            for i, atype in enumerate(anyof):
                ref_key = atype.get("$ref", None)
                if ref_key is not None:
                    ref = defs[ref_key.split("defs/")[-1]]
                    unpack_defs(ref, defs)
                    anyof[i] = ref
            continue

        items = value.get("items", None)
        if items is not None:
            ref_key = items.get("$ref", None)
            if ref_key is not None:
                ref = defs[ref_key.split("defs/")[-1]]
                unpack_defs(ref, defs)
                value["items"] = ref
                continue


def convert_to_nullable(schema):
    anyof = schema.pop("anyOf", None)
    if anyof is not None:
        if len(anyof) != 2:
            raise ValueError(
                "Invalid input: Type Unions are not supported, except for `Optional` types. "
                "Please provide an `Optional` type or a non-Union type."
            )
        a, b = anyof
        if a == {"type": "null"}:
            schema.update(b)
        elif b == {"type": "null"}:
            schema.update(a)
        else:
            raise ValueError(
                "Invalid input: Type Unions are not supported, except for `Optional` types. "
                "Please provide an `Optional` type or a non-Union type."
            )
        schema["nullable"] = True

    properties = schema.get("properties", None)
    if properties is not None:
        for name, value in properties.items():
            convert_to_nullable(value)

    items = schema.get("items", None)
    if items is not None:
        convert_to_nullable(items)


def add_object_type(schema):
    properties = schema.get("properties", None)
    if properties is not None:
        if "required" in schema and schema["required"] is None:
            schema.pop("required", None)
        schema["type"] = "object"
        for name, value in properties.items():
            add_object_type(value)

    items = schema.get("items", None)
    if items is not None:
        add_object_type(items)


def strip_field(schema, field_name: str):
    schema.pop(field_name, None)

    properties = schema.get("properties", None)
    if properties is not None:
        for name, value in properties.items():
            strip_field(value, field_name)

    items = schema.get("items", None)
    if items is not None:
        strip_field(items, field_name)


def _convert_vertex_datetime_to_openai_datetime(vertex_datetime: str) -> int:
    """
    Converts a Vertex AI datetime string to an OpenAI datetime integer

    vertex_datetime: str = "2024-12-04T21:53:12.120184Z"
    returns: int = 1722729192
    """
    from datetime import datetime

    # Parse the ISO format string to datetime object
    dt = datetime.strptime(vertex_datetime, "%Y-%m-%dT%H:%M:%S.%fZ")
    # Convert to Unix timestamp (seconds since epoch)
    return int(dt.timestamp())


def get_vertex_project_id_from_url(url: str) -> Optional[str]:
    """
    Get the vertex project id from the url

    `https://${LOCATION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${LOCATION}/publishers/google/models/${MODEL_ID}:streamGenerateContent`
    """
    match = re.search(r"/projects/([^/]+)", url)
    return match.group(1) if match else None


def get_vertex_location_from_url(url: str) -> Optional[str]:
    """
    Get the vertex location from the url

    `https://${LOCATION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${LOCATION}/publishers/google/models/${MODEL_ID}:streamGenerateContent`
    """
    match = re.search(r"/locations/([^/]+)", url)
    return match.group(1) if match else None


def replace_project_and_location_in_route(
    requested_route: str, vertex_project: str, vertex_location: str
) -> str:
    """
    Replace project and location values in the route with the provided values
    """
    # Replace project and location values while keeping route structure
    modified_route = re.sub(
        r"/projects/[^/]+/locations/[^/]+/",
        f"/projects/{vertex_project}/locations/{vertex_location}/",
        requested_route,
    )
    return modified_route


def construct_target_url(
    base_url: str,
    requested_route: str,
    vertex_location: Optional[str],
    vertex_project: Optional[str],
) -> httpx.URL:
    """
    Allow user to specify their own project id / location.

    If missing, use defaults

    Handle cachedContent scenario - https://github.com/BerriAI/litellm/issues/5460

    Constructed Url:
    POST https://LOCATION-aiplatform.googleapis.com/{version}/projects/PROJECT_ID/locations/LOCATION/cachedContents
    """
    new_base_url = httpx.URL(base_url)
    if "locations" in requested_route:  # contains the target project id + location
        if vertex_project and vertex_location:
            requested_route = replace_project_and_location_in_route(
                requested_route, vertex_project, vertex_location
            )
        return new_base_url.copy_with(path=requested_route)

    """
    - Add endpoint version (e.g. v1beta for cachedContent, v1 for rest)
    - Add default project id
    - Add default location
    """
    vertex_version: Literal["v1", "v1beta1"] = "v1"
    if "cachedContent" in requested_route:
        vertex_version = "v1beta1"

    base_requested_route = "{}/projects/{}/locations/{}".format(
        vertex_version, vertex_project, vertex_location
    )

    updated_requested_route = "/" + base_requested_route + requested_route

    updated_url = new_base_url.copy_with(path=updated_requested_route)
    return updated_url
