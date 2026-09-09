# Сетевая политика сессии и список доменов для вайтлиста

**Правило (пользователь, 2026-09-09):** домены, закрытые прокси среды, пользователь добавляет
в вайтлист списком из этого файла. **Обход запрещён** (зеркала, прокси, снятие TLS — Ц3).
**403/000/407 от прокси не игнорируются:** каждый закрытый домен записывается сюда с указанием,
зачем нужен, и факт с него помечается НЕПРОВЕРЕНО до открытия (Ц4, Ц10).

Как проверять: `curl -sS -o /dev/null -w '%{http_code}' --max-time 20 https://<домен>/` —
`000`/`403`/`407` = закрыт. Статус прокси: `curl -sS "$HTTPS_PROXY/__agentproxy/status"`.

Статусы: `закрыт` — проверено curl/агентом в сессии 1; `нужен` — ещё не проверяли, потребуется.

## A. Нужны для разработки (без них спринт 1–2 не запускается)
| Домен | Зачем | Статус |
|---|---|---|
| api.anthropic.com | вызовы Claude (скоринг, извлечение, ответы) | нужен |
| platform.claude.com, docs.anthropic.com | документация API | platform.claude.com открыт; anthropic.com закрыт |
| api.hubapi.com, developers.hubspot.com | HubSpot CRM API + доки | developers.hubspot.com закрыт |
| *.supabase.co, supabase.com | база данных, доки | supabase.com закрыт |
| vercel.com, api.vercel.com | деплой дашборда/API | vercel.com закрыт |
| www.dubaipulse.gov.ae, dubaipulse.gov.ae | открытый реестр лицензий DET (outbound-адаптер) | закрыт (HTTP 000) |
| bayanat.ae, data.bayanat.ae | федеральные открытые данные, лицензия CC BY | закрыт |
| google.serper.dev, serper.dev | новости (план B для outbound) | нужен |
| pypi.org, files.pythonhosted.org, registry.npmjs.org | пакеты | нужен (проверить) |
| github.com, api.github.com | репо, Actions | открыт |

## B. Первоисточники SORP и рынка (для проверки фактов ресерча 01, 05)
| Домен | Зачем | Статус |
|---|---|---|
| sorp.ae, bc.sorp.ae, bcenter.sorp.ae, sorptaxaccounting.com | услуги, цены, форматы БЦ, языки, чат-бот | закрыт |
| bayut.com, propertyfinder.ae, dubai.dubizzle.com | листинги SORP, цены за sq ft, вакансия | закрыт |
| matchoffice.com, easyoffices.com | агрегаторы, где листится SORP | нужен |
| regus.com, servcorp.ae, iwgplc.com | конкуренты, брокерские программы | закрыт (iwgplc) |
| instagram.com | @sorp_business_center | нужен |

## C. Реестры и право ОАЭ (ресерч 02, 06, 07)
| Домен | Зачем | Статус |
|---|---|---|
| u.ae, ner.economy.ae, growth.gov.ae | Национальный экономический реестр | закрыт |
| invest.dubai.ae, app.invest.dubai.ae | верификация лицензий DET | нужен |
| difc.com, dmcc.ae, dmccsf.my.site.com, dso.ae, ifza.com, meydanfz.ae, rakez.com, compass.rakez.com | фризоны: реестры, визовые лимиты, адреса | закрыт (difc, dmcc, dso, meydanfz) |
| mohre.gov.ae, gdrfad.gov.ae | визовая квота на площадь (первоисточник) | нужен |
| tdra.gov.ae, uaelegislation.gov.ae, moet.gov.ae | антиспам-политика, Res. 56/57/2024, PDPL | закрыт |
| dubaichambers.com, mediaoffice.ae | статистика новых компаний | закрыт (dubaichambers) |
| practiceguides.chambers.com, clydeco.com, legal500.com | правовые обзоры PDPL/телемаркетинг | закрыт |

## D. Медиа и данные (ресерч 06)
| Домен | Зачем | Статус |
|---|---|---|
| gulfnews.com, khaleejtimes.com, arabianbusiness.com, zawya.com, gulfbusiness.com | EN-новости «opens Dubai office», статистика DET | закрыт (gulfnews, khaleejtimes) |
| businessemirates.ae, russianemirates.family, news.uppersetup.com, kommersant.ru, forbes.ru, rbc.ru, vc.ru | RU-новости о выходе бизнеса в ОАЭ | закрыт (businessemirates, kommersant, forbes) |
| linkedin.com | только чтение публичных страниц компаний; скрейпинг не делаем | закрыт |
| bayt.com, gulftalent.com | вакансии как сигнал найма | закрыт |
| kaggle.com | датасет вакансий ОАЭ для демо | нужен |

## E. Инструменты и бенчмарки (ресерч 02, 03, 07)
| Домен | Зачем | Статус |
|---|---|---|
| apify.com, n8n.io, docs.n8n.io, make.com, retool.com, docs.streamlit.io, airtable.com | лимиты free-тарифов, лицензии | закрыт |
| resend.com, lemlist.com, instantly.ai, hunter.io, apidocs.bitrix24.com, developers.pipedrive.com | ToS отправки, API CRM | закрыт |
| gong.io, belkins.io, sopro.io, wordstream.com | бенчмарки outbound и CPL | закрыт (gong, belkins, sopro) |
| developers.google.com, learn.microsoft.com | Places API, LinkedIn API доки | закрыт |
| savills.com, jll.com, cbre.com | отчёты по офисному рынку | закрыт |
| 2gis.ae, klerk.ru, kazanforum.ru, dev.to | второстепенные | закрыт |

## Журнал закрытых доменов по ходу работы (append-only)
- 2026-09-09: список выше собран из отчётов агентов 01–07 и curl-проверки dubaipulse/bayanat.
- 2026-09-09, ресерч 08: hubspot.com, resolve247.ai, bitrix24.com, helpdesk.bitrix24.com, crm.org, layer3labs.io, respond.io, wati.io, leadar-uae.com, proppilot.ai, korvax.ai, propertyfinder.ae (PF Expert), semnexus.com, yowox.com, theaiagentindex.com, nomadx.ae, innovatrixinfotech.com, gravitybase.ai — нужны для официальных прайсов и лимитов.
