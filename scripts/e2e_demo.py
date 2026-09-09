"""Сквозная проверка обещания из первого абзаца README.

README обещает ровно это: сырой текст обращения превращается в карточку, с которой
менеджер может работать за секунды — что просят, сколько людей, какой срок, какой язык,
приоритет с причинами, дословные цитаты как доказательство и черновик ответа на языке
клиента; и ничего не отправляется клиенту само.

Этот скрипт проверяет каждое из перечисленного как отдельное утверждение и печатает
три исхода (Р1): выполнено / не выполнено / не смогли проверить. Он не заменяет тесты —
тесты проверяют части, а он проверяет обещание целиком, на живых провайдере и хранилище.

Запуск:  PYTHONPATH=. python3 scripts/e2e_demo.py [--offline]
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date

from leadcentre.engine import lint as lint_mod
from leadcentre.engine import extract as extract_mod
from leadcentre.engine.reply import draft
from leadcentre.engine.score import score_inbound
from leadcentre.models import InboundMessage

# Обращения, на которых проверяется обещание. Не самые удобные, а с краёв (Т3):
# срочный корпоративный, короткий вопрос о цене и спам.
CASES = (
    (
        "e2e-1",
        "whatsapp",
        "добрый день! переезжаем командой 8 человек, нужен офис в TECOM с 1 октября, "
        "плюс визы на всех. бюджет до 150 тысяч дирхам в год. когда можем обсудить?",
    ),
    ("e2e-2", "jivo", "скок стоит фриз зона?"),
    ("e2e-3", "telegram", "Здравствуйте! Продвигаем сайты в топ Google, 30% скидка."),
)


@dataclass
class Check:
    name: str
    outcome: str  # ok | fail | unknown
    detail: str


def check(name: str, condition: bool | None, detail: str) -> Check:
    if condition is None:
        return Check(name, "unknown", detail)
    return Check(name, "ok" if condition else "fail", detail)


def run_case(external_id: str, channel: str, text: str, today: date) -> list[Check]:
    message = InboundMessage(external_id, channel, text, today)
    checks: list[Check] = []
    started = time.time()
    try:
        facts = extract_mod.extract(message)
    except extract_mod.ExtractionError as error:
        return [Check("извлечение фактов", "unknown", f"{type(error).__name__}: {error}")]
    seconds = time.time() - started

    checks.append(check("ответ за секунды", seconds < 30, f"{seconds:.1f} с"))
    checks.append(
        check(
            "поняли, что просят",
            bool(facts.request_types) or facts.is_spam,
            f"типы={[t.value for t in facts.request_types]} спам={facts.is_spam}",
        )
    )
    checks.append(check("язык клиента определён", bool(facts.language), facts.language))

    score = score_inbound(message, facts)
    checks.append(
        check(
            "приоритет с причинами",
            bool(score.reasons) or score.tier.value == "LOW",
            f"{score.tier.value}: {'; '.join(score.reasons) or 'причин нет'}",
        )
    )
    quoted = [e.value for e in score.evidence if e.kind == "quote"]
    verbatim = all(q in text for q in quoted)
    checks.append(
        check(
            "цитаты дословны",
            verbatim if quoted else (score.tier.value != "HIGH"),
            f"{len(quoted)} шт., дословны={verbatim}",
        )
    )
    checks.append(
        check("HIGH только с доказательством", not (score.tier.value == "HIGH" and not quoted), "")
    )

    reply = draft(message, facts, score.tier.value)
    if reply.outcome == "spam_skipped":
        checks.append(check("на спам черновик не пишется", not reply.body.strip(), reply.outcome))
    else:
        checks.append(
            check(
                "черновик на языке клиента",
                reply.language == facts.language,
                f"черновик {reply.language}, обращение {facts.language}",
            )
        )
        result = lint_mod.lint(reply)
        checks.append(check("черновик проходит линтер", result.summary().find("нарушений 0") > 0,
                            result.summary()))
    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", default=date.today().isoformat())
    args = parser.parse_args(argv)
    today = date.fromisoformat(args.today)

    total = {"ok": 0, "fail": 0, "unknown": 0}
    for external_id, channel, text in CASES:
        print(f"\n=== {external_id} ({channel}) :: {text[:64]}…")
        for item in run_case(external_id, channel, text, today):
            mark = {"ok": "  ок    ", "fail": "  ПРОВАЛ", "unknown": "  не смогли"}[item.outcome]
            total[item.outcome] += 1
            print(f"{mark} {item.name:32} {item.detail}")

    print(
        f"\nпроверено {sum(total.values())}, выполнено {total['ok']}, "
        f"не выполнено {total['fail']}, не смогли проверить {total['unknown']}"
    )
    if total["fail"]:
        return 1
    return 2 if total["unknown"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
