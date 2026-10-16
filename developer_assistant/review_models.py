import html
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .llm import InvalidModelResponse


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal["bug", "architecture", "recommendation"]
    severity: Literal["high", "medium", "low"]
    path: str = Field(min_length=1)
    side: Literal["head", "base"]
    line_start: int | None
    line_end: int | None
    title: str = Field(min_length=1, max_length=200)
    explanation: str = Field(min_length=1, max_length=3000)
    scenario: str = Field(min_length=1, max_length=2000)
    suggestion: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence_level: Literal["confirmed", "hypothesis"]
    references: list[str] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def line_range(self):
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("Both line boundaries must be null or integers")
        if self.line_start is not None and not 1 <= self.line_start <= self.line_end:
            raise ValueError("Invalid line range")
        return self


class BatchReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    findings: list[Finding] = Field(max_length=40)


def validate_findings(answer, context):
    if not isinstance(answer, BatchReview):
        try:
            answer = BatchReview.model_validate(answer, strict=True)
        except Exception as exc:
            raise InvalidModelResponse("Некорректная схема ревью") from exc
    sources = {s["id"]: s for s in context["sources"]}
    for finding in answer.findings:
        files = [f for f in context["files"] if f[finding.side + "_path"] == finding.path]
        if not files:
            raise InvalidModelResponse("Замечание ссылается на неизвестный путь или сторону")
        file = files[0]
        if any(ref not in sources for ref in finding.references):
            raise InvalidModelResponse("Замечание ссылается на неизвестный источник")
        if finding.line_start is not None:
            count = file[finding.side + "_lines"]
            if count is None or finding.line_end > count:
                raise InvalidModelResponse("Номер строки вне файла")
            ranges = [r for source in context["sources"]
                      if source.get(finding.side + "_path") == finding.path
                      for r in source.get(finding.side + "_ranges", [])]
            ranges += [[s["start_line"], s["end_line"]] for s in context["sources"]
                       if s.get("kind") in {"code", "documentation"} and finding.side == "head"
                       and s.get("path") == finding.path]
            if not any(a <= finding.line_start <= finding.line_end <= b for a, b in ranges):
                raise InvalidModelResponse("Строки замечания отсутствовали в переданном контексте")
    return answer


def deduplicate(findings):
    result, seen = [], set()
    for f in findings:
        key = (f["category"], f["path"], f["side"], f["line_start"], f["line_end"],
               re.sub(r"\W+", " ", f["title"].lower()).strip())
        if key not in seen:
            seen.add(key)
            result.append(f)
    return result


def safe(text):
    # No active HTML, images or model-generated links in the artifact.
    return html.escape(str(text)).replace("[", "\\[").replace("]", "\\]").replace("`", "\\`")


def write_report(report, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report["findings"] = deduplicate(report["findings"])
    scope = report["scope"]
    lines = ["# AI Code Review", "", "## Область проверки", "",
             f"Статус: **{report['status']}**.", "",
             f"- Base: `{scope['base']}`", f"- Head: `{scope['head']}`",
             f"- Merge-base (сторона base): `{scope.get('merge_base') or 'не определён'}`",
             f"- Изменено файлов: {scope['changed_files']}; проверено полностью: {scope['reviewed_files']}; частично: {scope['partial_files']}.",
             f"- MCP: {scope['mcp']}; RAG: {scope['rag']}; LLM: {scope['llm']}.",
             f"- Модель: {safe(scope.get('model') or 'не вызывалась')}.",
             "- Скрипты, сборка и тесты анализируемого проекта не запускались.", ""]
    if report.get("error"):
        lines.extend(["Техническая ошибка: " + safe(report["error"]), ""])
    for category, title in [("bug", "Потенциальные баги"), ("architecture", "Архитектурные проблемы"),
                            ("recommendation", "Рекомендации")]:
        lines.extend(["## " + title, ""])
        findings = [f for f in report["findings"] if f["category"] == category]
        if not findings:
            message = ("Обоснованных замечаний в проверенной части не найдено."
                       if report["status"] in {"completed", "partial"} and scope["llm"]
                       else "Замечаний нет в отчёте; генерация ревью не завершена или не требовалась.")
            lines.extend([message, ""])
        for f in findings:
            location = safe(f["path"]) + " (" + f["side"] + ")"
            if f["line_start"] is not None:
                location += f":{f['line_start']}-{f['line_end']}"
            lines.extend([f"### [{f['severity']}] {safe(f['title'])}", "", location, "",
                          safe(f["explanation"]), "", "Сценарий и последствия: " + safe(f["scenario"]), "",
                          "Исправление: " + safe(f["suggestion"]), "",
                          f"Подтверждение: {f['evidence_level']}; confidence: {f['confidence']}.", "",
                          "Источники: " + "; ".join(safe(ref) for ref in f["references"]), ""])
    lines.extend(["## Пропущенные файлы и ограничения", ""])
    limitations = report["limitations"]
    lines.extend(["- " + safe(v) for v in limitations] or ["Дополнительных ограничений не зафиксировано."])
    for item in report["skipped"]:
        lines.append(f"- {safe(item['path'])}: {safe(item['reason'])}")
    if report.get("index_skipped"):
        lines.extend(["", "### Исключено из RAG", ""])
        lines.extend(f"- {safe(x['path'])}: {safe(x['reason'])}" for x in report["index_skipped"])
    for path, data in [(output, "\n".join(lines) + "\n"),
                       (output.with_suffix(".json"), json.dumps(report, ensure_ascii=False, indent=2) + "\n")]:
        pending = path.with_suffix(path.suffix + ".tmp")
        pending.write_text(data, encoding="utf-8")
        pending.replace(path)
