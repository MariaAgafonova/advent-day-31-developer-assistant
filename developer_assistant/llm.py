"""Shared structured transport for documentation answers and reviews."""
import json

from .config import AssistantError


class InvalidModelResponse(AssistantError):
    pass


def structured_response(settings, instructions, context, schema, *, max_tokens=None, json_hint=""):
    from openai import OpenAI, APIConnectionError, APITimeoutError, APIStatusError
    try:
        with OpenAI(api_key=settings.api_key, base_url=settings.base_url,
                    timeout=settings.timeout, max_retries=1) as client:
            if settings.provider == "DeepSeek":
                rules = '\nВерни только JSON без Markdown, по следующей JSON schema: '
                rules += json.dumps(schema.model_json_schema(), ensure_ascii=False) + json_hint
                result = client.chat.completions.create(
                    model=settings.model,
                    messages=[{"role": "system", "content": instructions + rules},
                              {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                    response_format={"type": "json_object"}, extra_body={"thinking": {"type": "disabled"}},
                    max_tokens=max_tokens or settings.max_tokens,
                )
                if not result.choices:
                    raise InvalidModelResponse("DeepSeek вернул пустой ответ.")
                choice = result.choices[0]
                if choice.finish_reason == "length":
                    raise InvalidModelResponse("Ответ DeepSeek обрезан. Увеличьте лимит токенов.")
                if choice.finish_reason != "stop" or not choice.message.content:
                    raise InvalidModelResponse("DeepSeek не вернул завершённый ответ.")
                answer = schema.model_validate_json(choice.message.content, strict=True)
            else:
                result = client.responses.parse(
                    model=settings.model, instructions=instructions,
                    input=json.dumps(context, ensure_ascii=False), text_format=schema,
                    max_output_tokens=max_tokens or settings.max_tokens, store=False,
                )
                answer = result.output_parsed
    except (APIConnectionError, APITimeoutError) as exc:
        raise AssistantError(f"{settings.provider} API недоступен или превышен таймаут. Проверьте подключение и повторите запрос.") from exc
    except APIStatusError as exc:
        raise AssistantError(f"{settings.provider} API вернул HTTP {exc.status_code}. Проверьте ключ, доступ к модели и баланс/лимиты аккаунта.") from exc
    except AssistantError:
        raise
    except Exception as exc:
        raise InvalidModelResponse(f"Не удалось получить корректный структурированный ответ {settings.provider}.") from exc
    if answer is None:
        raise InvalidModelResponse(f"{settings.provider} не вернул завершённый структурированный ответ.")
    return answer
