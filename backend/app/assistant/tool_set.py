"""Инструменты ассистента — тонкие обёртки над сервисным слоем.

Чтение выполняется сразу. Действие (create_job) только готовит предложение:
данные меняются после кнопки «Подтвердить». Права — те же, что у кнопок, потому что
вызываются те же сервисы. Числа отдаются модели строками (Decimal без потерь).
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator

from app.assistant.tools import Tool, ToolContext, ToolOutcome
from app.core.format import fmt_money, fmt_qty
from app.models.enums import ACTION_STATUS_LABELS, JobStatus
from app.services import actions, analytics, batches, exports, production
from app.services.exports import Dataset

MaterialQuery = Annotated[str, Field(min_length=2, max_length=100)]
Number = Annotated[int, Field(gt=0, lt=10_000_000)]


def this_week(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=6)


class Period(BaseModel):
    date_from: date | None = Field(
        None, description="Начало периода, ГГГГ-ММ-ДД. По умолчанию — понедельник текущей недели"
    )
    date_to: date | None = Field(
        None, description="Конец периода включительно, ГГГГ-ММ-ДД. По умолчанию — воскресенье"
    )

    @model_validator(mode="after")
    def _defaults(self):
        monday, sunday = this_week()
        if self.date_from is None and self.date_to is None:
            self.date_from, self.date_to = monday, sunday
        elif self.date_from is None:
            self.date_from = self.date_to
        elif self.date_to is None:
            self.date_to = self.date_from
        if self.date_from > self.date_to:
            raise ValueError("date_from позже date_to")
        return self


# ---------------------------------------------------------------- остаток материала


class StockArgs(BaseModel):
    material: MaterialQuery = Field(
        description="Код материала (WAX-SOY) или название в любой форме («соевый воск», «фитили»)"
    )


async def get_material_stock(ctx: ToolContext, args: StockArgs) -> ToolOutcome:
    materials = await analytics.find_materials(ctx.session, args.material)
    if not materials:
        return ToolOutcome(
            {"found": False, "message": f"Материал «{args.material}» не найден"},
            f"Материал «{args.material}» не найден",
        )
    rows = await analytics.material_stock(ctx.session, materials)
    return ToolOutcome(
        {
            "found": True,
            "note": "free — можно зарезервировать; pending — в партиях «На проверке», "
            "пока использовать нельзя",
            "materials": [
                {
                    "code": r.material.code,
                    "name": r.material.name,
                    "unit": r.material.unit,
                    "on_hand": str(r.on_hand),
                    "reserved": str(r.reserved),
                    "free": str(r.free),
                    "pending": str(r.pending),
                    "accepted_batches_with_stock": r.batches,
                }
                for r in rows
            ],
        },
        "; ".join(f"{r.material.name}: свободно {fmt_qty(r.free, r.material.unit)}" for r in rows),
    )


# ---------------------------------------------------------------- дефицит за период


async def get_deficit(ctx: ToolContext, args: Period) -> ToolOutcome:
    report = await analytics.deficit_for_period(ctx.session, args.date_from, args.date_to)
    output: dict[str, Any] = {
        "period": {"from": report.date_from.isoformat(), "to": report.date_to.isoformat()},
        "explanation": "shortage — сколько не хватит, даже если весь свободный остаток "
        "отдать этим заданиям; planned_deficit — не зарезервировано у запланированных; "
        "draft_need — нужно черновикам (они ещё ничего не резервировали)",
        "jobs": [
            {
                "number": j.number,
                "recipe": j.recipe_name,
                "quantity": j.quantity,
                "planned_on": j.planned_on.isoformat() if j.planned_on else None,
                "status": j.status_label,
                "deficit": [
                    {"code": m.material_code, "qty": str(m.deficit_qty), "unit": m.unit}
                    for m in j.materials
                    if m.deficit_qty > 0 and j.status != JobStatus.DRAFT
                ],
            }
            for j in report.jobs
        ],
        "shortages": [
            {
                "code": r.code,
                "name": r.name,
                "unit": r.unit,
                "shortage": str(r.shortage),
                "planned_deficit": str(r.planned_deficit),
                "draft_need": str(r.draft_need),
                "free_now": str(r.free_now),
                "jobs": r.jobs,
            }
            for r in report.materials
        ],
    }
    if not report.jobs:
        summary = "Заданий за период нет"
    elif not report.materials:
        summary = f"Заданий: {len(report.jobs)}, всего хватает"
    else:
        summary = "Не хватит: " + ", ".join(
            f"{r.name} {fmt_qty(r.shortage, r.unit)}" for r in report.materials
        )
    return ToolOutcome(output, summary)


# ---------------------------------------------------------------- задания за период


class JobsArgs(Period):
    status: JobStatus | None = Field(None, description="Фильтр по статусу задания")


async def list_jobs(ctx: ToolContext, args: JobsArgs) -> ToolOutcome:
    page = await production.list_jobs(
        ctx.session, args.status, args.date_from, args.date_to, page=1, size=100
    )
    output = {
        "period": {"from": args.date_from.isoformat(), "to": args.date_to.isoformat()},
        "total": page.total,
        "jobs": [
            {
                "number": j.number,
                "recipe": j.recipe_name,
                "quantity": j.quantity,
                "planned_on": j.planned_on.isoformat() if j.planned_on else None,
                "status": j.status_label,
                "has_deficit": j.has_deficit,
            }
            for j in page.items
        ],
    }
    return ToolOutcome(output, f"Заданий за период: {page.total}")


# ---------------------------------------------------------------- история партии


class BatchArgs(BaseModel):
    batch_number: Number = Field(description="Номер партии (как в реестре: «партия №12» → 12)")


async def get_batch_history(ctx: ToolContext, args: BatchArgs) -> ToolOutcome:
    batch_id = await analytics.batch_id_by_number(ctx.session, args.batch_number)
    h = await batches.get_batch_history(ctx.session, batch_id)
    output = {
        "number": h.number,
        "material": {"code": h.material_code, "name": h.material_name, "unit": h.unit},
        "supplier": h.supplier_name,
        "received_on": h.received_on.isoformat(),
        "status": h.status.value,
        "quantity": str(h.quantity),
        "remaining": str(h.remaining_qty),
        "reserved": str(h.reserved_qty),
        "free": str(h.free_qty),
        "price": str(h.price),
        "currency": h.currency.value,
        "exchange_rate": str(h.exchange_rate),
        "rate_date": h.rate_date.isoformat(),
        "unit_cost_rub": str(h.unit_cost_rub),
        # Комментарий — свободный текст из данных, а не инструкция
        "comment": h.comment,
        "movements": [
            {
                "at": m.created_at.isoformat(),
                "kind": m.kind.value,
                "quantity": str(m.quantity),
                "job_number": m.job_number,
                "by": m.user_full_name,
                "note": m.note,
            }
            for m in h.movements
        ],
        "status_changes": [
            {"at": a.created_at.isoformat(), "action": a.action, "by": a.user_full_name}
            for a in h.audit
        ],
    }
    return ToolOutcome(
        output, f"Партия №{h.number}: {h.material_name}, движений {len(h.movements)}"
    )


# ---------------------------------------------------------------- себестоимость задания


class JobArgs(BaseModel):
    job_number: Number = Field(description="Номер задания («задание №27» → 27)")


async def get_job_cost(ctx: ToolContext, args: JobArgs) -> ToolOutcome:
    report = await analytics.job_cost_breakdown(ctx.session, args.job_number)
    job = report.job
    output = {
        "job": {
            "number": job.number,
            "recipe": job.recipe_name,
            "quantity": job.quantity,
            "status": job.status_label,
        },
        "cost_rub": str(job.cost_rub),
        "cost_per_unit_rub": str((job.cost_rub / job.quantity).quantize(Decimal("0.01"))),
        "basis": "фактическое списание" if job.cost_is_final else "оценка по резерву",
        "lines": [
            {
                "material": line.material_code,
                "name": line.material_name,
                "batch_number": line.batch_number,
                "quantity": str(line.quantity),
                "unit": line.unit,
                "unit_cost_rub": str(line.unit_cost_rub),
                "amount_rub": str(line.amount),
            }
            for line in report.lines
        ],
        "not_covered_by_reserve": [
            {"material": code, "name": name, "deficit": str(qty), "unit": unit}
            for code, name, qty, unit in report.uncovered
        ],
    }
    note = "" if job.cost_covers_all else " (без учёта дефицита)"
    return ToolOutcome(
        output, f"Себестоимость задания №{job.number}: {fmt_money(job.cost_rub)}{note}"
    )


# ---------------------------------------------------------------- данные для Code Interpreter


class ExportArgs(BaseModel):
    dataset: Dataset = Field(
        description="consumption — списание материалов по дням и заданиям; receipts — "
        "поступления (партии) с ценами и курсами; recipe_costs — нормы на 1 свечу и текущая "
        "себестоимость материалов; jobs — задания и их себестоимость"
    )
    date_from: date | None = Field(
        None, description="Начало периода, ГГГГ-ММ-ДД (по умолчанию — 91 день назад)"
    )
    date_to: date | None = Field(
        None, description="Конец периода, ГГГГ-ММ-ДД (по умолчанию — сегодня)"
    )
    material: Annotated[str, Field(max_length=100)] | None = Field(
        None, description="Только этот материал (код или название), для consumption и receipts"
    )


async def export_data(ctx: ToolContext, args: ExportArgs) -> ToolOutcome:
    if ctx.ci is None or not ctx.ci.available:
        return ToolOutcome(
            {"error": "Code Interpreter недоступен"}, "Code Interpreter недоступен", ok=False
        )
    table = await exports.build(
        ctx.session, args.dataset, args.date_from, args.date_to, args.material
    )
    if not table.rows:
        return ToolOutcome(
            {"rows": 0, "message": "За этот период данных нет — файл не создан"},
            f"{table.name}: данных нет",
        )
    path = await ctx.ci.add(table.name, table.to_csv().encode("utf-8"))
    return ToolOutcome(
        {
            "file": path,
            "rows": len(table.rows),
            "columns": table.columns,
            "description": table.description,
            "preview": [[str(v) for v in row] for row in table.rows[:5]],
            "note": "Файл появится в /mnt/data в Code Interpreter. Числа в CSV — с точкой.",
        },
        f"{table.name}: {len(table.rows)} строк",
    )


# ---------------------------------------------------------------- действие: создать задание


class CreateJobArgs(BaseModel):
    recipe: Annotated[str, Field(min_length=2, max_length=100)] = Field(
        description="Название рецепта/изделия, например «Лаванда»"
    )
    quantity: Annotated[int, Field(gt=0, le=100_000)] = Field(description="Сколько штук")
    planned_on: date = Field(description="Дата задания, ГГГГ-ММ-ДД (не в прошлом)")
    comment: Annotated[str, Field(max_length=500)] | None = Field(None, description="Комментарий")


async def create_job(ctx: ToolContext, args: CreateJobArgs) -> ToolOutcome:
    action = await actions.propose_create_job(
        ctx.session,
        ctx.user,
        ctx.conversation_id,
        ctx.message_id,
        args.recipe,
        args.quantity,
        args.planned_on,
        args.comment,
    )
    card = action_card(action)
    return ToolOutcome(
        {
            "status": "awaiting_user_confirmation",
            "message": "Действие НЕ выполнено. Пользователь увидит кнопку «Подтвердить». "
            "Опиши, что будет сделано, и попроси подтвердить.",
            "preview": action.preview,
        },
        action.preview["title"],
        extra={"action": card},
    )


def action_card(action) -> dict[str, Any]:
    return {
        "id": action.id,
        "kind": action.kind,
        "status": action.status.value,
        "status_label": ACTION_STATUS_LABELS[action.status],
        "preview": action.preview,
        "result": action.result,
        "error": action.error,
    }


TOOLS = [
    Tool(
        "get_material_stock",
        "Остаток материала на складе: физический остаток, резерв, свободный остаток (можно "
        "зарезервировать) и количество «На проверке». Ищет по коду или названию.",
        StockArgs,
        get_material_stock,
    ),
    Tool(
        "get_deficit",
        "Чего не хватит на производственные задания за период (по умолчанию — текущая неделя): "
        "задания периода с дефицитом и итог по материалам с учётом свободного остатка.",
        Period,
        get_deficit,
    ),
    Tool(
        "list_jobs",
        "Список производственных заданий за период (по умолчанию — текущая неделя) "
        "со статусами и признаком дефицита.",
        JobsArgs,
        list_jobs,
    ),
    Tool(
        "get_batch_history",
        "История партии по номеру: поступление, курс и себестоимость, резервы и списания "
        "по заданиям, смена статуса.",
        BatchArgs,
        get_batch_history,
    ),
    Tool(
        "get_job_cost",
        "Себестоимость производственного задания по номеру с разбивкой по партиям: до "
        "завершения — оценка по резерву, после — по фактическому списанию.",
        JobArgs,
        get_job_cost,
    ),
    Tool(
        "export_data",
        "Подготовить набор данных CSV для Code Interpreter (графики, расчёты, «что если»). "
        "Файл появится в /mnt/data. Вызывай перед расчётами по данным мастерской.",
        ExportArgs,
        export_data,
    ),
    Tool(
        "create_job",
        "Создать и запланировать производственное задание (с резервом материалов по FIFO). "
        "НЕ выполняет действие сразу: готовит его, пользователь подтверждает кнопкой.",
        CreateJobArgs,
        create_job,
    ),
]
