"""Калькулятор экономики Lead Centre (CONCEPT_v5 §4, §6). Запуск: python3 docs/concept/calc.py
Все входы — ВЫБРАНО, кроме формулы; владелец подставляет свои. Числа в концепции — вывод этого скрипта."""
K_ACK = 0.3          # ВЫБРАНО: доля эффекта быстрого ответа, которую даёт авто-квитанция без цены
MARGIN = 0.5         # ВЫБРАНО: маржа вклада от выручки первого года (вопрос владельцу)
R = {"с офисом": 87_000, "без офиса": 42_000}          # РАСЧЁТ, ресерч 07 §1
T = {"A": 3_000, "B": 6_500, "C": 6_600}               # AED/мес, CONCEPT_v5 §6
SCEN = {"A": (0.55, 0.0), "B": (0.75, 0.0), "C": (0.75, 0.15)}  # (h′ человека, покрытие квитанцией)
CASES = {
    "консервативно": dict(I=150, h=0.40, cf=0.25, cs=0.12, c=0.20),
    "пессимистично": dict(I=80, h=0.55, cf=0.18, cs=0.13, c=0.15),
}

def delta_k(p, h_human, ack):
    return max(0.0, p["I"] * ((h_human - p["h"]) + K_ACK * ack) * (p["cf"] - p["cs"]) * p["c"])

if __name__ == "__main__":
    for case, p in CASES.items():
        for s, (hh, ack) in SCEN.items():
            dk = delta_k(p, hh, ack)
            cells = []
            for name, r in R.items():
                thr = T[s] / (r * MARGIN)
                cells.append(f"{name}: V={dk*r/1000:.1f}k порог={thr:.3f} {'OK' if dk >= thr else 'НЕТ'}")
            print(f"{case:14s} {s}  ΔK={dk:.3f}  " + " | ".join(cells))
