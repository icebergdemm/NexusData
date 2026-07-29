"""
Заводская конфигурация проверщика кораблей (ГОСТ КБ «Гайка»).

Всё, что здесь лежит, — это ТОЛЬКО значения по умолчанию: реальная
конфигурация хранится в БД (models.SubCheckerConfig) и правится через
вкладку «Чекер кораблей» → «Настройки проверок»/«Классы» суперпользователем
или обладателем права sub_checker:manage_rules / manage_classes.
DEFAULT_CONFIG используется при первом обращении (посев) и при явном сбросе
настроек к заводским.

Источники данных:
  * формулы и пороги оборудования — Submarine checker v0.96 (Team Zero Tools),
    разобранный исполняемый файл ../чекер/.../sub_checker.py;
  * классы, наполнение, запрещённые предметы — docs/«ГОСТ КБ _ГАЙКА_.docx».

Идентификаторы предметов сверены с реальными .sub-файлами флота
(«Вектор [ASPER]» — класс «Средний», «Бресиджер [ASPER]» — класс «Катер»):
их наполнение совпало с таблицами ГОСТа позиция в позицию, поэтому
соответствие «русское название → identifier» здесь не угадано, а проверено.
Позиции, для которых в этих файлах идентификатора не нашлось, помечены
enabled=False и note — включить их можно после уточнения идентификатора.
"""

from typing import Any

# ── Единицы измерения ────────────────────────────────────────────────────────
# В .sub всё в игровых единицах (1 единица = 1 см). dimensions="Д,В" в см.
# rect у Hull = "x,y,ширина,высота" в см, площадь считается в см².

DEFAULT_FORMULAS: dict[str, dict[str, Any]] = {
    "length_m": {
        "label": "Длина корабля, м",
        "expr": "length_cm / 100",
        "help": "length_cm — первое число атрибута dimensions.",
    },
    "height_m": {
        "label": "Высота корабля, м",
        "expr": "height_cm / 100",
        "help": "height_cm — второе число атрибута dimensions.",
    },
    "raw_area": {
        "label": "Габаритная площадь, м²",
        "expr": "length_cm * height_cm / 10000",
        "help": "Площадь описанного прямоугольника.",
    },
    "hull_area": {
        "label": "Площадь отсеков, м²",
        "expr": "hulls_cm2 / 10000",
        "help": "Сумма площадей всех Hull.",
    },
    "factor": {
        "label": "Коэффициент заполнения",
        "expr": "raw_area / hull_area",
        "help": "Во сколько раз габарит больше суммарной площади отсеков.",
    },
    "volume": {
        "label": "Условный объём",
        "expr": "length_cm * height_cm * (length_cm + height_cm) / 2000000",
        "help": "Промежуточная величина для расчёта тоннажа.",
    },
    "tonnage": {
        "label": "Тоннаж, т",
        "expr": "volume / factor",
        "help": "Именно по этой величине определяется класс корабля (таб. 1 ГОСТа).",
    },
    "max_speed": {
        "label": "Максимальная скорость, км/ч",
        "expr": "sqrt(total_engine_force * 1000 * 0.1 * 40 / (2 * height_cm * height_cm * 0.01)) * 3.6",
        "help": "total_engine_force — сумма MaxForce всех двигателей.",
    },
    "optimal_ballast": {
        "label": "Оптимальный уровень балласта",
        "expr": "hulls_cm2 * 0.07 / ballast_cm2",
        "help": "Значение NeutralBallastLevel для расстыкованного корабля.",
    },
    "power_load": {
        "label": "Загрузка энергосистемы",
        "expr": "total_power_consumption / total_power_output",
        "help": "Потребление (без турелей и разрядников) делить на мощность реакторов.",
    },
}

# ── Оборудование ─────────────────────────────────────────────────────────────

DEFAULT_EQUIPMENT: dict[str, Any] = {
    "reactors": {
        "label": "Реакторы",
        "base_power_default": 5000.0,
        "base_power_by_type": {"outpostreactor": 10000.0},
        # Минимально допустимый Scale при данном MaxPowerOutput.
        "min_scale_expr": "ceil(5.0 * sqrt(max_power_output / base_power)) / 10",
        "min_fuel_consumption_rate": 0.2,
    },
    "engines": {
        "label": "Двигатели",
        "min_scale": 0.5,
        "max_force_default": 50.0,
        "max_force_by_type": {"largeengine": 100.0, "shuttleengine": 50.0},
        "power_factor_default": 35.0,
        # ГОСТ 9.1: челнок — MaxForce*20, обычный и большой — MaxForce*35.
        "power_factor_by_type": {"largeengine": 25.0, "shuttleengine": 20.0},
        "min_power_expr": "ceil(max_force * power_factor)",
    },
    "pumps": {
        "label": "Насосы",
        "allowed_types": ["pump", "smallpump"],
        "base_flow_default": 100.0,
        "base_flow_by_type": {"pump": 300.0},
        "power_factor_default": 0.6,
        "power_factor_by_type": {"pump": 0.5},
        "min_scale_expr": "ceil(5.0 * sqrt(max_flow / base_flow)) / 10",
        "min_power_expr": "ceil(max_flow * power_factor)",
        "ballast_tag": "ballast",
    },
    "batteries": {
        "label": "Батареи и суперконденсаторы",
        # ГОСТ 8.8: Масштаб = 0.5 * √(Ёмкость / базовая ёмкость)
        "min_scale_expr": "0.5 * sqrt(capacity / base_capacity)",
        "base_capacity_default": 1000.0,
        "base_capacity_by_type": {"battery": 1000.0, "supercapacitor": 10.0},
        "scale_tolerance": 0.01,
    },
    "oxygen_generators": {
        "label": "Генераторы кислорода",
        # ГОСТ 11: таблица «производство O2 → потребление».
        # Для каждого генератора берётся первая строка, в которую он влезает
        # по GeneratedAmount; PowerConsumption должен быть не меньше указанного.
        "tiers": [
            {"max_output": 500.0, "min_power": 1000.0},
            {"max_output": 750.0, "min_power": 1200.0},
            {"max_output": 1000.0, "min_power": 1500.0},
            {"max_output": 1500.0, "min_power": 2000.0},
        ],
    },
    "power": {
        "label": "Энергосистема",
        # ГОСТ 8.2: распределительный щиток — в единственном экземпляре.
        "max_power_distributors": 1,
        # ГОСТ 8.5: мощность реактора рекомендуется держать в рамках затрат.
        "power_load_min": 0.5,
        "power_load_max": 1.0,
        # Компоненты, чьё PowerConsumption не входит в суммарное потребление
        # (турели и разрядники стреляют импульсно).
        "consumption_excluded_tags": ["ElectricalDischarger", "Turret"],
    },
}

# ── Автозаполнение (ГОСТ 6.2) ────────────────────────────────────────────────

DEFAULT_AUTOFILL: dict[str, Any] = {
    # Контейнеры с этими тегами автозаполнение иметь ВПРАВЕ — это боеприпасы,
    # топливо и торговые автоматы, которые иначе пришлось бы набивать руками.
    "exempt_tags": [
        "ammobox", "vendingmachine", "reactorfuel", "smgammo",
        "railgunammo", "depthchargeammo",
    ],
}

# ── Запрещённые предметы (ГОСТ 15) ───────────────────────────────────────────
# Список унаследован от Submarine checker v0.96 и сверен с разделом 15 ГОСТа.
# Совпадение по вхождению подстроки в identifier — как в оригинальном чекере,
# поэтому "geneticmaterial" ловит все ген-материалы, "dementonite" — весь
# дементонит и т.д.

_FORBIDDEN_RAW = [
    ("dirtybomb", "Грязная бомба"),
    ("40mmchemgrenade", "40-мм кислотная граната"),
    ("autoshotgun", "Автоматический дробовик"),
    ("machinepistol", "Пистолет-пулемёт (машинный)"),
    ("oxygenitetank", "Баллон с кислородитом"),
    ("banditoutfit", "Одежда бандита"),
    ("coalitioncommendation", "Награда Коалиции"),
    ("combatdivingsuit", "Боевой водолазный костюм"),
    ("combatstimulantsyringe", "Боевой стимулятор"),
    ("defensebotammobox", "Боеприпасы защитного бота"),
    ("handcannon", "Ручная пушка"),
    ("piratebodyarmor", "Пиратский бронежилет"),
    ("nucleargun", "Ядерное орудие"),
    ("clownexosuit", "Клоунский экзоскелет"),
    ("abyssdivingsuit", "Водолазный скафандр бездны"),
    ("autoinjectorheadset", "Наушники с инжектором"),
    ("geneticmaterial", "Любой генетический материал"),
    ("endocrinebooster", "Гормональный усилитель"),
    ("fulguriumbatterycell", "Грозовиевый аккумулятор"),
    ("fulguriumfuelrod", "Грозовиевый топливный стержень"),
    ("screwdriverdementonite", "Дементонитовая отвёртка"),
    ("wrenchdementonite", "Дементонитовый гаечный ключ"),
    ("crowbardementonite", "Дементонитовый лом"),
    ("divingknifedementonite", "Дементонитовый водолазный нож"),
    ("ancientweapon", "Древнее оружие"),
    ("arcemitter", "Дуговой разрядник"),
    ("skillbookeuropanmedicine", "Учебник: медицина Европы"),
    ("huskstinger", "Жало хаска"),
    ("ironhelmet", "Железный шлем"),
    ("screwdriverhardened", "Закалённая отвёртка"),
    ("wrenchhardened", "Закалённый гаечный ключ"),
    ("crowbarhardened", "Закалённый лом"),
    ("divingknifehardened", "Закалённый водолазный нож"),
    ("alienpistol", "Инопланетный пистолет"),
    ("alienflare", "Инопланетный сигнальный огонь"),
    ("skillbooksubmarinewarfare", "Учебник: подводная война"),
    ("smgmagazinedepletedfuel", "Магазин ПП с обеднённым топливом"),
    ("crawlermask", "Маска ползуна"),
    ("coalitionmedal", "Медаль Коалиции"),
    ("depletedfuel", "Обеднённое топливо"),
    ("respawndivingsuit", "Костюм челнока возрождения"),
    ("electriciansgoggles", "Очки электрика"),
    ("revolverrounddepletedfuel", "Патрон револьвера с обеднённым топливом"),
    ("clowndivingmask", "Клоунская подводная маска"),
    ("riotshield", "Полицейский щит"),
    ("reactorpda", "КПК реактора"),
    ("explosiveslug", "Разрывная пуля"),
    ("repairpack", "Мультитул"),
    ("rituallantern", "Ритуальная лампа"),
    ("handhelditemfinder", "Ручной искатель предметов"),
    ("handheldstatusmonitor", "Ручной монитор состояния"),
    ("handheldelectricalmonitor", "Ручной монитор электросети"),
    ("cultistrobes", "Одеяние культиста"),
    ("zealotrobes", "Одеяние фанатика"),
    ("makeshiftarmor", "Самодельный бронежилет"),
    ("genesplicer", "Спайщик генов"),
    ("skillbooksailorsguide", "Учебник: пособие моряка"),
    ("thermalgoggles", "Тепловизор"),
    ("incendiumfueltank", "Баллон с воспламенителем"),
    ("hmg", "Тяжёлый пулемёт"),
    ("heavywrench", "Тяжёлый гаечный ключ"),
    ("advancedgenesplicer", "Улучшенный спайщик генов"),
    ("advancedsyringegun", "Улучшенный шприц-пистолет"),
    ("skillbookhandyseaman", "Учебник: умелый моряк"),
    ("shellshield", "Щит из панциря"),
    ("chitinhelmet", "Хитиновый шлем"),
    ("scrap", "Хлам"),
    ("scrapcannon", "Хлам-пушка"),
    ("ceremonialsword", "Церемониальный меч"),
    ("spinelingspike", "Шип спайнлинга"),
    ("mudraptorshell", "Панцирь грязевого раптора"),
    ("piratehelmet", "Пиратский шлем"),
    ("piratecaptainhat", "Шляпа пиратского капитана"),
    ("assaultrifle", "Штурмовая винтовка"),
    ("exosuit", "Экзоскелет"),
    ("huskeggs", "Экстракт каликса"),
    ("40mmnuke", "40-мм ядерный снаряд"),
    ("huskeggsbasic", "Яйца велонацепса каликса"),
    ("coilgunammoboxdepletedfuel", "Боеприпасы с обеднённым топливом"),
]

# Две позиции из унаследованного списка прямо конфликтуют с разделом 13 ГОСТа
# (пожарный шкаф ОБЯЗАН содержать абордажный топор и пенные гранаты), поэтому
# по умолчанию они выключены, но остаются в списке — включаются одной галочкой.
_FORBIDDEN_CONFLICTING = {
    "boardingaxe": ("Абордажный топор", "Конфликт с ГОСТ 13: требуется в пожарном шкафу."),
    "fixfoamgrenade": ("Граната с монтажной пеной", "Конфликт с ГОСТ 13: требуется в пожарном шкафу."),
}

DEFAULT_FORBIDDEN_ITEMS: list[dict[str, Any]] = (
    [{"identifier": i, "label": lbl, "enabled": True, "note": ""} for i, lbl in _FORBIDDEN_RAW]
    + [
        {"identifier": i, "label": lbl, "enabled": False, "note": note}
        for i, (lbl, note) in _FORBIDDEN_CONFLICTING.items()
    ]
)

# ── Правила ──────────────────────────────────────────────────────────────────
# severity: error (нарушение ГОСТа) | warning (рекомендация) | info (к сведению)

DEFAULT_RULES: list[dict[str, Any]] = [
    {"id": "dimensions", "label": "Габариты корабля заданы корректно",
     "category": "geometry", "severity": "error", "enabled": True,
     "description": "dimensions и суммарная площадь отсеков должны быть больше нуля, иначе тоннаж и класс посчитать нельзя."},
    {"id": "manually_outfitted", "label": "Галочка «Снабжение вручную»",
     "category": "general", "severity": "error", "enabled": True,
     "description": "ГОСТ 1.5: корабль должен сохраняться с ismanuallyoutfitted=true."},
    {"id": "indestructible_walls", "label": "Неразрушаемые стены не допускаются",
     "category": "geometry", "severity": "error", "enabled": True,
     "description": "ГОСТ 3.3: Structure с Indestructible=True запрещены."},
    {"id": "reactor_scale", "label": "Масштаб реактора",
     "category": "power", "severity": "error", "enabled": True,
     "description": "Scale реактора не должен быть меньше расчётного для его MaxPowerOutput."},
    {"id": "reactor_fuel_rate", "label": "Расход топлива реактора",
     "category": "power", "severity": "error", "enabled": True,
     "description": "ГОСТ 8.4: FuelConsumptionRate не меньше 0,2."},
    {"id": "reactor_emp", "label": "Реактор уязвим к ЭМИ",
     "category": "power", "severity": "error", "enabled": True,
     "description": "ГОСТ 8.3: VulnerableToEMP=True."},
    {"id": "power_distributors", "label": "Распределительный щиток в единственном экземпляре",
     "category": "power", "severity": "error", "enabled": True,
     "description": "ГОСТ 8.2."},
    {"id": "power_load", "label": "Мощность реактора в рамках затрат энергосистемы",
     "category": "power", "severity": "warning", "enabled": True,
     "description": "ГОСТ 8.5 — рекомендация. Загрузка вне диапазона отмечается предупреждением."},
    {"id": "battery_scale", "label": "Масштаб батарей и суперконденсаторов",
     "category": "power", "severity": "error", "enabled": True,
     "description": "ГОСТ 8.8: Масштаб = 0.5·√(Ёмкость/базовая ёмкость)."},
    {"id": "engine_scale", "label": "Масштаб двигателя",
     "category": "engines", "severity": "error", "enabled": True,
     "description": "Scale двигателя не меньше минимального."},
    {"id": "engine_force", "label": "Максимальная сила двигателя",
     "category": "engines", "severity": "error", "enabled": True,
     "description": "ГОСТ 9.3: MaxForce не выше предела для типа двигателя."},
    {"id": "engine_power", "label": "Потребление двигателя",
     "category": "engines", "severity": "error", "enabled": True,
     "description": "ГОСТ 9.1: PowerConsumption не меньше MaxForce × коэффициент."},
    {"id": "engine_propeller_damage", "label": "Двигатель наносит урон винтом",
     "category": "engines", "severity": "error", "enabled": True,
     "description": "DisablePropellerDamage должен быть False."},
    {"id": "engine_emp", "label": "Двигатель уязвим к ЭМИ",
     "category": "engines", "severity": "error", "enabled": True,
     "description": "ГОСТ 9.2."},
    {"id": "pump_allowed_types", "label": "Разрешённые типы насосов",
     "category": "pumps", "severity": "error", "enabled": True,
     "description": "ГОСТ 10.1: только «Насос» и «Малый насос»."},
    {"id": "pump_scale", "label": "Масштаб насоса",
     "category": "pumps", "severity": "error", "enabled": True,
     "description": "Scale насоса не меньше расчётного для его MaxFlow."},
    {"id": "pump_power", "label": "Потребление насоса",
     "category": "pumps", "severity": "error", "enabled": True,
     "description": "PowerConsumption не меньше MaxFlow × коэффициент."},
    {"id": "ballast_pump_tag", "label": "Метка «ballast» у балластных насосов",
     "category": "pumps", "severity": "error", "enabled": True,
     "description": "ГОСТ 10.2: насосы в балластных отсеках должны иметь метку ballast."},
    {"id": "ballast_level", "label": "Уровень балласта в терминале навигации",
     "category": "navigation", "severity": "warning", "enabled": True,
     "description": "NeutralBallastLevel должен совпадать с расчётным оптимальным."},
    {"id": "oxygen_generator", "label": "Потребление генератора кислорода",
     "category": "life_support", "severity": "error", "enabled": True,
     "description": "ГОСТ 11: потребление по таблице производства O₂."},
    {"id": "equipment_emp", "label": "Всё оборудование повреждается от ЭМИ",
     "category": "equipment", "severity": "error", "enabled": True,
     "description": "ГОСТ 7.1: у компонентов с VulnerableToEMP должно стоять True."},
    {"id": "autofill", "label": "Автозаполнение запрещено",
     "category": "equipment", "severity": "error", "enabled": True,
     "description": "ГОСТ 6.2, кроме контейнеров с разрешёнными тегами."},
    {"id": "forbidden_items", "label": "Запрещённые предметы",
     "category": "items", "severity": "error", "enabled": True,
     "description": "ГОСТ 15."},
    {"id": "class_match", "label": "Класс корабля определён по тоннажу",
     "category": "class", "severity": "error", "enabled": True,
     "description": "ГОСТ 1.4 / таб. 1: тоннаж должен попадать в один из классов."},
    {"id": "class_items", "label": "Наполнение по классу",
     "category": "class", "severity": "error", "enabled": True,
     "description": "Разделы 13 и 14 ГОСТа: обязательные предметы для класса корабля."},
    {"id": "class_manual", "label": "Пункты ручной проверки по классу",
     "category": "class", "severity": "info", "enabled": True,
     "description": "Требования таб. 1, которые нельзя проверить автоматически."},
]

# Общие пункты ручной проверки (не привязаны к классу).
DEFAULT_MANUAL_CHECKS: list[dict[str, Any]] = [
    {"id": "docs", "label": "Документация о системах корабля и подписи активных элементов", "enabled": True, "gost": "1.6"},
    {"id": "cover", "label": "Обложка соответствует кораблю", "enabled": True, "gost": "1.5"},
    {"id": "hull_drain", "label": "Отсеки осушаются полностью, «застрявшей воды» нет", "enabled": True, "gost": "3.2"},
    {"id": "double_plating", "label": "Прочность зоны двойной обшивки не выше одинарной", "enabled": True, "gost": "3.4"},
    {"id": "cheek_background", "label": "В щеках есть задний фон", "enabled": True, "gost": "3.5"},
    {"id": "spawnpoints", "label": "Точки появления для груза и экипажа с метками ГОСТа", "enabled": True, "gost": "4.1"},
    {"id": "id_tags", "label": "Метки должностей: asper_captain, asper_sb, asper_doctor, asper_crewmember, asper_firecontainer", "enabled": True, "gost": "4.2"},
    {"id": "id_colors", "label": "Цветовая палитра должностных удостоверений", "enabled": True, "gost": "4.3"},
    {"id": "spare_ids", "label": "В каждом защищаемом ящике ≥2 дополнительных пропуска", "enabled": True, "gost": "4.4"},
    {"id": "captain_safe", "label": "В сейфе капитана полный комплект пропусков + 2 удостоверения матроса", "enabled": True, "gost": "4.4"},
    {"id": "shuttle_dock", "label": "Корректная стыковка с челноком пополнения", "enabled": True, "gost": "5.1"},
    {"id": "radio_station", "label": "Установлена станция радиосвязи актуальной версии", "enabled": True, "gost": "5.3"},
    {"id": "radio_channels", "label": "Каналы 101 и 102 не используются", "enabled": True, "gost": "5.4"},
    {"id": "labels", "label": "Кнопки, рычаги и переключатели подписаны", "enabled": True, "gost": "6.3"},
    {"id": "suit_racks_power", "label": "Стойки со скафандрами и полки с баллонами не под напряжением", "enabled": True, "gost": "6.4"},
    {"id": "connection_panels", "label": "Панели подключений доступны, оборудование интерактивно", "enabled": True, "gost": "7.3"},
    {"id": "no_upgrades", "label": "Параметры оборудования не улучшены", "enabled": True, "gost": "7.4"},
    {"id": "light_colors", "label": "Нет кислотных цветов и пересвета в освещении", "enabled": True, "gost": "12.1"},
    {"id": "weak_spots", "label": "У корабля есть слабые стороны и уязвимые части", "enabled": True, "gost": "14.1"},
    {"id": "supercap_fire", "label": "Суперконденсаторы не дают бесконечную стрельбу при заряде <80%", "enabled": True, "gost": "14.2"},
    {"id": "weapon_counterweight", "label": "У усиливающих огневую мощь систем есть противовес", "enabled": True, "gost": "14.3"},
    {"id": "arsenal_storage", "label": "Оружие хранится разряженным в открытом доступе, патроны — в закрытом", "enabled": True, "gost": "14.6"},
    {"id": "duck", "label": "Приоткрытая вентиляция со спрятанной глубоководной уточкой", "enabled": True, "gost": "13"},
]


def _items(pairs: list[tuple], group: str) -> list[dict[str, Any]]:
    """(identifier, label, min) или (identifier, label, min, note) → требования."""
    out = []
    for p in pairs:
        identifier, label, minimum = p[0], p[1], p[2]
        note = p[3] if len(p) > 3 else ""
        out.append({
            "identifier": identifier,
            "label": label,
            "min": minimum,
            "max": None,
            # Позиции с note — те, для которых идентификатор не подтверждён
            # реальными .sub-файлами флота; включаются после уточнения.
            "enabled": not note,
            "note": note,
            "group": group,
        })
    return out


_UNVERIFIED = "Идентификатор не подтверждён — уточните и включите проверку."

# ── Наполнение по классам ────────────────────────────────────────────────────

_GENERAL = {
    "boat": [
        ("divingsuit", "Скафандр", 4), ("oxygentank", "Кислородный баллон", 12),
        ("thoriumfuelrod", "Ториевый стержень", 1), ("fuelrod", "Топливный стержень", 3),
        ("wrench", "Гаечный ключ", 1), ("screwdriver", "Отвёртка", 1),
        ("weldingtool", "Сварочный аппарат", 1), ("weldingfueltank", "Баллон для сварки", 4),
        ("plasmacutter", "Плазменный резак", 1), ("bluewire", "Синий провод", 8),
        ("relaycomponent", "Реле", 2), ("lever", "Рычаг", 1),
        ("sonarbeacon", "Сонарный маяк", 1), ("handheldsonar", "Ручной сонар", 1),
        ("flashlight", "Фонарик", 1), ("underwaterscooter", "Подводный скутер", 1),
        ("crowbar", "Лом", 1), ("batterycell", "Аккумулятор", 6),
        ("flare", "Сигнальный огонь", 2), ("artifacttransportcase", "Контейнер для артефактов", 1),
    ],
    "small": [
        ("divingsuit", "Скафандр", 4), ("oxygentank", "Кислородный баллон", 10),
        ("thoriumfuelrod", "Ториевый стержень", 1), ("fuelrod", "Топливный стержень", 3),
        ("wrench", "Гаечный ключ", 2), ("screwdriver", "Отвёртка", 2),
        ("weldingtool", "Сварочный аппарат", 2), ("weldingfueltank", "Баллон для сварки", 4),
        ("plasmacutter", "Плазменный резак", 1), ("bluewire", "Синий провод", 8),
        ("relaycomponent", "Реле", 1), ("fpgacircuit", "Схема на базе ППВМ", 2),
        ("lever", "Рычаг", 1), ("button", "Кнопка", 1),
        ("sonarbeacon", "Сонарный маяк", 1), ("handheldsonar", "Ручной сонар", 1),
        ("flashlight", "Фонарик", 2), ("underwaterscooter", "Подводный скутер", 2),
        ("crowbar", "Лом", 1), ("batterycell", "Аккумулятор", 8),
        ("flare", "Сигнальный огонь", 4), ("artifacttransportcase", "Контейнер для артефактов", 1),
    ],
    "medium": [
        ("divingsuit", "Скафандр", 4), ("oxygentank", "Кислородный баллон", 12),
        ("batterycell", "Аккумулятор", 9), ("thoriumfuelrod", "Ториевый стержень", 1),
        ("fuelrod", "Топливный стержень", 3), ("wrench", "Гаечный ключ", 2),
        ("screwdriver", "Отвёртка", 2), ("weldingtool", "Сварочный аппарат", 2),
        ("weldingfueltank", "Баллон для сварки", 4), ("plasmacutter", "Плазменный резак", 2),
        ("bluewire", "Синий провод", 8), ("relaycomponent", "Реле", 2),
        ("fpgacircuit", "Схема на базе ППВМ", 4), ("lever", "Рычаг", 1), ("button", "Кнопка", 1),
        ("sonarbeacon", "Сонарный маяк", 2), ("handheldsonar", "Ручной сонар", 2),
        ("flashlight", "Фонарик", 2), ("underwaterscooter", "Подводный скутер", 2),
        ("cargoscooter", "Грузовой скутер", 1), ("crowbar", "Лом", 1),
        ("flare", "Сигнальный огонь", 4), ("glowstick", "ХИС", 2),
        ("artifacttransportcase", "Контейнер для артефактов", 1),
    ],
    "large": [
        ("divingsuit", "Скафандр", 4), ("oxygentank", "Кислородный баллон", 18),
        ("batterycell", "Аккумулятор", 9), ("thoriumfuelrod", "Ториевый стержень", 1),
        ("fuelrod", "Топливный стержень", 3), ("wrench", "Гаечный ключ", 3),
        ("screwdriver", "Отвёртка", 3), ("weldingtool", "Сварочный аппарат", 3),
        ("weldingfueltank", "Баллон для сварки", 6), ("plasmacutter", "Плазменный резак", 4),
        ("bluewire", "Синий провод", 8), ("redwire", "Красный провод", 8),
        ("relaycomponent", "Реле", 2), ("fpgacircuit", "Схема на базе ППВМ", 6),
        ("lever", "Рычаг", 2), ("button", "Кнопка", 2),
        ("sonarbeacon", "Сонарный маяк", 2), ("handheldsonar", "Ручной сонар", 2),
        ("flashlight", "Фонарик", 3), ("underwaterscooter", "Подводный скутер", 3),
        ("cargoscooter", "Грузовой скутер", 1), ("crowbar", "Лом", 1),
        ("flare", "Сигнальный огонь", 6), ("glowstick", "ХИС", 4),
    ],
    "huge": [
        ("divingsuit", "Скафандр", 4), ("oxygentank", "Кислородный баллон", 24),
        ("batterycell", "Аккумулятор", 12), ("thoriumfuelrod", "Ториевый стержень", 2),
        ("fuelrod", "Топливный стержень", 6), ("wrench", "Гаечный ключ", 4),
        ("screwdriver", "Отвёртка", 4), ("weldingtool", "Сварочный аппарат", 4),
        ("weldingfueltank", "Баллон для сварки", 8), ("plasmacutter", "Плазменный резак", 4),
        ("bluewire", "Синий провод", 8), ("redwire", "Красный провод", 8),
        ("relaycomponent", "Реле", 2), ("fpgacircuit", "Схема на базе ППВМ", 8),
        ("lever", "Рычаг", 2), ("button", "Кнопка", 2),
        ("sonarbeacon", "Сонарный маяк", 2), ("handheldsonar", "Ручной сонар", 2),
        ("flashlight", "Фонарик", 4), ("underwaterscooter", "Подводный скутер", 4),
        ("cargoscooter", "Грузовой скутер", 1), ("crowbar", "Лом", 1),
        ("artifacttransportcase", "Контейнер для артефактов", 1),
        ("flare", "Сигнальный огонь", 8), ("glowstick", "ХИС", 4),
    ],
}

_MATERIALS = {
    "boat": [],
    "small": [
        ("aluminium", "Алюминий", 24), ("iron", "Железо", 32), ("silicon", "Кремний", 8),
        ("lithium", "Литий", 2), ("magnesium", "Магний", 4), ("copper", "Медь", 6),
        ("sodium", "Натрий", 2), ("tin", "Олово", 4), ("rubber", "Резина", 14),
        ("titanium", "Титан", 6), ("thorium", "Торий", 2), ("carbon", "Углерод", 20),
        ("uranium", "Уран", 4), ("phosphorus", "Фосфор", 2), ("zinc", "Цинк", 4),
        ("plastic", "Пластик", 4), ("lead", "Свинец", 12),
    ],
    "medium": [
        ("amblygonite", "Амблигонит", 2), ("chamosite", "Бавалин", 20), ("bornite", "Борнит", 3),
        ("brockite", "Брокит", 3), ("galena", "Галена", 8), ("graphite", "Графит", 11),
        ("cassiterite", "Касситерит", 1), ("quartz", "Кварц", 4), ("langbeinite", "Лангбейнит", 2),
        ("rubber", "Резина", 28), ("stannite", "Станнит", 2, _UNVERIFIED),
        ("sphalerite", "Сфалерит", 2), ("titanite", "Титанит", 4),
        ("uraniumore", "Урановая руда", 2), ("chrysoprase", "Хризопраз", 2),
    ],
    "large": [
        ("diamond", "Алмаз", 11), ("amblygonite", "Амблигонит", 4), ("chamosite", "Бавалин", 30),
        ("bornite", "Борнит", 6), ("brockite", "Брокит", 4), ("galena", "Галена", 12),
        ("ilmenite", "Ильменит", 2), ("cassiterite", "Касситерит", 3),
        ("langbeinite", "Лангбейнит", 2), ("rubber", "Резина", 48), ("sphalerite", "Сфалерит", 3),
        ("titanite", "Титанит", 6), ("uraniumore", "Урановая руда", 3), ("chrysoprase", "Хризопраз", 8),
    ],
    "huge": [
        ("diamond", "Алмаз", 16), ("amblygonite", "Амблигонит", 6), ("chamosite", "Бавалин", 41),
        ("bornite", "Борнит", 8), ("brockite", "Брокит", 6), ("galena", "Галена", 16),
        ("ilmenite", "Ильменит", 4), ("cassiterite", "Касситерит", 3),
        ("langbeinite", "Лангбейнит", 2), ("rubber", "Резина", 70), ("sphalerite", "Сфалерит", 4),
        ("titanite", "Титанит", 8), ("uraniumore", "Урановая руда", 4), ("chrysoprase", "Хризопраз", 8),
    ],
}

_MEDICAL = {
    "boat": [
        ("antidama1", "Морфин", 10), ("opium", "Опиум", 6), ("antinarc", "Налоксон", 2),
        ("calyxanide", "Каликсанид", 1), ("stabilozine", "Стабилоцин", 8),
        ("deusizine", "Деусицин", 1), ("morbusineantidote", "Антидот морбузина", 1),
        ("antipsychosis", "Галоперидол", 1), ("adrenaline", "Адреналин", 2),
        ("antibloodloss1", "Физраствор", 10), ("antibloodloss2", "Пакет с кровью", 4),
        ("antibleeding1", "Бинт", 16), ("antibleeding2", "Эластичный бинт", 4),
        ("antibleeding3", "Антибиотический клей", 1), ("ethanol", "Этанол", 2),
        ("antirad", "Антирад", 2), ("antibiotics", "Антибиотик", 6),
        ("alienblood", "Инопланетная кровь", 2), ("antiparalysis", "Анапарализатор", 1),
        ("tonicliquid", "Жидкий тоник", 2),
    ],
    "small": [
        ("antidama1", "Морфин", 14), ("opium", "Опиум", 8), ("antinarc", "Налоксон", 4),
        ("calyxanide", "Каликсанид", 2), ("stabilozine", "Стабилоцин", 12),
        ("morbusineantidote", "Антидот морбузина", 1), ("sufforinantidote", "Антидот страданита", 1),
        ("cyanideantidote", "Антидот цианида", 1), ("antipsychosis", "Галоперидол", 2),
        ("deusizine", "Деусицин", 2), ("antibloodloss1", "Физраствор", 16),
        ("antibloodloss2", "Пакет с кровью", 6), ("antibleeding1", "Бинт", 20),
        ("antibleeding2", "Эластичный бинт", 6), ("antibleeding3", "Антибиотический клей", 1),
        ("ethanol", "Этанол", 4), ("healthscanner", "Информационный сканер", 1),
        ("steroids", "Анаболический стероид", 1), ("hyperzine", "Гиперцин", 1),
        ("antirad", "Антирад", 2), ("antibiotics", "Антибиотик", 10),
        ("alienblood", "Инопланетная кровь", 4), ("chloralhydrate", "Хлоралгидрат", 2),
        ("antiparalysis", "Анапарализатор", 2), ("tonicliquid", "Жидкий тоник", 2),
    ],
    "medium": [
        ("antidama1", "Морфин", 16), ("antinarc", "Налоксон", 6), ("stabilozine", "Стабилоцин", 4),
        ("antibiotics", "Антибиотик", 4), ("ethanol", "Этанол", 2),
        ("antibloodloss1", "Физраствор", 6), ("antibloodloss2", "Пакет с кровью", 8),
        ("antibleeding3", "Антибиотический клей", 2), ("antibleeding1", "Бинт", 18),
        ("antibleeding2", "Эластичный бинт", 10), ("steroids", "Анаболический стероид", 2),
        ("adrenaline", "Адреналин", 4), ("antidama2", "Фентанил", 2),
        ("deusizine", "Деусицин", 2), ("cyanide", "Цианид", 1), ("sufforin", "Страданит", 1),
        ("sufforinantidote", "Антидот страданита", 1), ("cyanideantidote", "Антидот цианида", 1),
        ("morbusineantidote", "Антидот морбузина", 2), ("antirad", "Антирад", 3),
        ("hyperzine", "Гиперцин", 1), ("calyxanide", "Каликсанид", 2),
        ("tonicliquid", "Жидкий тоник", 3), ("chloralhydrate", "Хлоралгидрат", 3),
        ("antiparalysis", "Анапарализатор", 3), ("healthscanner", "Информационный сканер", 1),
        ("antipsychosis", "Галоперидол", 2), ("liquidoxygenite", "Кислородит", 2),
    ],
    "large": [
        ("antidama1", "Морфин", 10), ("opium", "Опиум", 8), ("antinarc", "Налоксон", 4),
        ("calyxanide", "Каликсанид", 3), ("stabilozine", "Стабилоцин", 6),
        ("deusizine", "Деусицин", 3), ("morbusineantidote", "Антидот морбузина", 2),
        ("sufforinantidote", "Антидот страданита", 2), ("cyanideantidote", "Антидот цианида", 2),
        ("antirad", "Антирад", 4), ("radiotoxin", "Радиотоксин", 2, _UNVERIFIED),
        ("sufforin", "Страданит", 2), ("cyanide", "Цианид", 2),
        ("antibloodloss1", "Физраствор", 12), ("antibloodloss2", "Пакет с кровью", 8),
        ("antibleeding1", "Бинт", 12), ("antibleeding2", "Эластичный бинт", 4),
        ("antibleeding3", "Антибиотический клей", 2), ("ethanol", "Этанол", 8),
        ("healthscanner", "Информационный сканер", 1), ("steroids", "Анаболический стероид", 2),
        ("hyperzine", "Гиперцин", 2), ("antiparalysis", "Анапарализатор", 2),
        ("antipsychosis", "Галоперидол", 3), ("antidama2", "Фентанил", 2),
        ("tonicliquid", "Жидкий тоник", 4), ("antibiotics", "Антибиотик", 6),
    ],
    "huge": [
        ("antidama1", "Морфин", 16), ("opium", "Опиум", 10), ("antinarc", "Налоксон", 6),
        ("calyxanide", "Каликсанид", 4), ("stabilozine", "Стабилоцин", 12),
        ("deusizine", "Деусицин", 4), ("morbusineantidote", "Антидот морбузина", 2),
        ("sufforinantidote", "Антидот страданита", 2), ("cyanideantidote", "Антидот цианида", 2),
        ("antirad", "Антирад", 4), ("sufforin", "Страданит", 2), ("cyanide", "Цианид", 2),
        ("radiotoxin", "Радиотоксин", 2, _UNVERIFIED),
        ("antibloodloss1", "Физраствор", 18), ("antibloodloss2", "Пакет с кровью", 6),
        ("antibleeding1", "Бинт", 16), ("antibleeding2", "Эластичный бинт", 8),
        ("antibleeding3", "Антибиотический клей", 3), ("ethanol", "Этанол", 6),
        ("healthscanner", "Сканер здоровья", 2), ("steroids", "Анаболический стероид", 4),
        ("hyperzine", "Гиперцин", 3), ("antiparalysis", "Анапарализатор", 4),
        ("antipsychosis", "Галоперидол", 4), ("antidama2", "Фентанил", 3),
        ("tonicliquid", "Жидкий тоник", 6), ("antibiotics", "Антибиотик", 10),
    ],
}

_MED_MATERIALS = {
    "boat": [],
    "small": [],
    "medium": [
        ("aquaticpoppy", "Водяной мак", 3), ("swimbladder", "Плавательный пузырь", 5),
        ("slimebacteria", "Слизистые бактерии", 3), ("yeastshroom", "Морские дрожжи", 3),
        ("fiberplant", "Прядильное растение", 3), ("sulphuricacid", "Серная кислота", 4),
        ("alienblood", "Инопланетная кровь", 6),
    ],
    "large": [
        ("aquaticpoppy", "Водяной мак", 14), ("swimbladder", "Плавательный пузырь", 8),
        ("slimebacteria", "Слизистые бактерии", 4), ("oxygeniteshard", "Осколок кислородита", 2),
        ("fiberplant", "Прядильное растение", 5), ("elastin", "Эластиновое растение", 4, _UNVERIFIED),
        ("yeastshroom", "Морские дрожжи", 4), ("pyromorphite", "Пироморфит", 5, _UNVERIFIED),
        ("calyxegg", "Кладка яиц каликса", 3, _UNVERIFIED),
        ("sulphuricacid", "Серная кислота", 4), ("alienblood", "Инопланетная кровь", 8),
        ("aragonite", "Арагонит", 2, _UNVERIFIED),
        ("adrenalinegland", "Адреналиновая железа", 4),
        ("buttontoxin", "Галлюциногенный бутотоксин", 2, _UNVERIFIED),
    ],
    "huge": [
        ("aquaticpoppy", "Водяной мак", 16), ("swimbladder", "Плавательный пузырь", 8),
        ("alienblood", "Инопланетная кровь", 10), ("slimebacteria", "Слизистые бактерии", 4),
        ("fiberplant", "Прядильное растение", 6), ("elastin", "Эластиновое растение", 5, _UNVERIFIED),
        ("yeastshroom", "Морские дрожжи", 4), ("sulphuricacid", "Серная кислота", 5),
        ("oxygeniteshard", "Осколок кислородита", 3), ("calyxegg", "Кладка яиц каликса", 4, _UNVERIFIED),
        ("aragonite", "Арагонит", 3, _UNVERIFIED), ("adrenalinegland", "Адреналиновая железа", 7),
        ("pyromorphite", "Пироморфит", 7, _UNVERIFIED),
        ("buttontoxin", "Галлюциногенный бутотоксин", 2, _UNVERIFIED),
    ],
}

_ARSENAL_LOCKED = {
    "boat": [
        ("fraggrenade", "Осколочная граната", 1), ("stungrenade", "Светошумовая граната", 1),
        ("smgmagazine", "Магазин для ПП", 4), ("shotgunshell", "Дробь", 18),
        ("stungundart", "Дротик", 6), ("handcuffs", "Наручники", 2),
    ],
    "small": [
        ("fraggrenade", "Осколочная граната", 1), ("stungrenade", "Светошумовая граната", 1),
        ("smgmagazine", "Магазин для ПП", 4), ("shotgunshell", "Дробь", 18),
        ("revolverround", "Патрон для револьвера", 12), ("stungundart", "Дротик", 6),
        ("c4block", "С-4", 1), ("handcuffs", "Наручники", 2),
    ],
    "medium": [
        ("fraggrenade", "Осколочная граната", 2), ("stungrenade", "Светошумовая граната", 2),
        ("smgmagazine", "Магазин для ПП", 5), ("shotgunshell", "Дробь", 24),
        ("revolverround", "Патрон для револьвера", 12), ("riflebullet", "Патрон для ружья", 12),
        ("stungundart", "Дротик", 8), ("c4block", "С-4", 1),
        ("handcuffs", "Наручники", 4), ("uex", "UEX", 1),
    ],
    "large": [
        ("fraggrenade", "Осколочная граната", 3), ("stungrenade", "Светошумовая граната", 4),
        ("smgmagazine", "Магазин для ПП", 8), ("shotgunshell", "Дробь", 30),
        ("revolverround", "Патрон для револьвера", 12), ("riflebullet", "Патрон для ружья", 12),
        ("stungundart", "Дротик", 8), ("c4block", "С-4", 2),
        ("handcuffs", "Наручники", 5), ("uex", "UEX", 2),
    ],
    "huge": [
        ("fraggrenade", "Осколочная граната", 3), ("stungrenade", "Светошумовая граната", 4),
        ("smgmagazine", "Магазин для ПП", 8), ("shotgunshell", "Дробь", 30),
        ("revolverround", "Патрон для револьвера", 12), ("riflebullet", "Патрон для ружья", 18),
        ("stungundart", "Дротик", 8), ("c4block", "С-4", 2),
        ("handcuffs", "Наручники", 5), ("uex", "UEX", 2),
    ],
}

_ARSENAL_OPEN = {
    "boat": [
        ("smg", "Пистолет-пулемёт", 1), ("stungun", "Электрошокер", 1), ("shotgun", "Дробовик", 1),
        ("ballistichelmet2", "Шлем стрелка", 1), ("ballistichelmet1", "Пуленепробиваемый шлем", 1),
        ("stunbaton", "Шоковая дубинка", 2), ("prisonerclothes", "Форма заключённого", 1),
    ],
    "small": [
        ("smg", "Пистолет-пулемёт", 1), ("stungun", "Электрошокер", 1), ("shotgun", "Дробовик", 1),
        ("ballistichelmet2", "Шлем стрелка", 1), ("ballistichelmet1", "Пуленепробиваемый шлем", 1),
        ("stunbaton", "Шоковая дубинка", 2), ("prisonerclothes", "Форма заключённого", 2),
        ("revolver", "Револьвер", 1),
    ],
    "medium": [
        ("smg", "Пистолет-пулемёт", 1), ("stungun", "Электрошокер", 2), ("shotgun", "Дробовик", 1),
        ("ballistichelmet2", "Шлем стрелка", 2), ("ballistichelmet1", "Пуленепробиваемый шлем", 1),
        ("stunbaton", "Шоковая дубинка", 2), ("prisonerclothes", "Форма заключённого", 2),
        ("revolver", "Револьвер", 1), ("rifle", "Ружьё", 1), ("bodyarmor", "Бронежилет", 2),
    ],
    "large": [
        ("smg", "Пистолет-пулемёт", 2), ("stungun", "Электрошокер", 3), ("shotgun", "Дробовик", 1),
        ("ballistichelmet2", "Шлем стрелка", 2), ("ballistichelmet1", "Пуленепробиваемый шлем", 2),
        ("stunbaton", "Шоковая дубинка", 4), ("prisonerclothes", "Форма заключённого", 3),
        ("revolver", "Револьвер", 1), ("rifle", "Ружьё", 1), ("bodyarmor", "Бронежилет", 3),
    ],
    "huge": [
        ("smg", "Пистолет-пулемёт", 2), ("stungun", "Электрошокер", 3), ("shotgun", "Дробовик", 1),
        ("ballistichelmet2", "Шлем стрелка", 2), ("ballistichelmet1", "Пуленепробиваемый шлем", 2),
        ("stunbaton", "Шоковая дубинка", 4), ("prisonerclothes", "Форма заключённого", 4),
        ("revolver", "Револьвер", 1), ("rifle", "Ружьё", 1), ("bodyarmor", "Бронежилет", 4),
    ],
}

# ГОСТ 13.2: малый пожарный шкаф допускается на катерах и малых кораблях.
_FIRE_SMALL = [
    ("fixfoamgrenade", "Пенная граната", 2), ("crowbar", "Лом", 1),
    ("divingmask", "Кислородная маска", 1), ("oxygentank", "Кислородный баллон", 1),
    ("flare", "Сигнальный огонь", 4), ("extinguisher", "Огнетушитель", 1),
]
_FIRE_STANDARD = [
    ("fixfoamgrenade", "Пенная граната", 4), ("boardingaxe", "Абордажный топор", 1),
    ("crowbar", "Лом", 1), ("divingmask", "Кислородная маска", 2),
    ("oxygentank", "Кислородный баллон", 2), ("flare", "Сигнальный огонь", 4),
    ("extinguisher", "Огнетушитель", 1),
]
_FIRE = {"boat": _FIRE_SMALL, "small": _FIRE_SMALL,
         "medium": _FIRE_STANDARD, "large": _FIRE_STANDARD, "huge": _FIRE_STANDARD}

_CLASS_META = [
    ("boat", "Катер", 1000, 2000,
     "Производство отсутствует",
     "Малокалиберные орудия с возможностью модификации. Катушки ИЛИ сброс глубоководных бомб",
     "Медпункт (шкаф для токсинов). Возможно наличие мед. кровати",
     "Возможна совмещённая с реактором электрощитовая"),
    ("small", "Малый", 2000, 5000,
     "Неполная линия. Мед. фабрикатор отсутствует",
     "Малокалиберные орудия с возможностью модификации. Большие орудия с фиксированным углом обзора. Катушки ИЛИ сброс глубоководных бомб",
     "Медпункт (шкаф для токсинов). Возможно наличие мед. кровати",
     "Возможна совмещённая с реактором электрощитовая"),
    ("medium", "Средний", 5000, 10000,
     "Полная линия. Мед. фабрикатор отсутствует",
     "Малокалиберные орудия с возможностью модификации. Сбалансированное большое орудие с возможностью модификации. Катушки И сброс глубоководных бомб",
     "Медотсек с койкой и медпунктом (шкаф для хранения токсинов)",
     "Раздельная с реактором электрощитовая"),
    ("large", "Большой", 10000, 20000,
     "Полная линия. Мед. фабрикатор",
     "Малокалиберные орудия с возможностью модификации. Сбалансированные большие орудия с возможностью модификации. Катушки И сброс глубоководных бомб",
     "Медотсек с койкой, мед. фабрикатором и медпунктом (шкаф для хранения токсинов)",
     "Раздельная с реактором электрощитовая"),
    ("huge", "Сверхбольшой", 20000, None,
     "Полная линия. Мед. фабрикатор. Разрешается дополнительное оборудование",
     "Малокалиберные орудия с возможностью модификации. Сбалансированные большие орудия с возможностью модификации. Катушки И сброс глубоководных бомб",
     "Отдельный медпункт, отдельный медотсек с койкой, отдельный отсек с мед. фабрикатором",
     "Раздельная с реактором электрощитовая"),
]

_GROUP_LABELS = [
    ("general", "Общее наполнение корабля", _GENERAL),
    ("materials", "Материалы для производства", _MATERIALS),
    ("medical", "Наполнение медицины", _MEDICAL),
    ("med_materials", "Материалы для производства медицины", _MED_MATERIALS),
    ("arsenal_locked", "Арсенал: закрытый доступ", _ARSENAL_LOCKED),
    ("arsenal_open", "Арсенал: открытый доступ", _ARSENAL_OPEN),
    ("fire_cabinet", "Пожарный шкаф", _FIRE),
]


def _build_classes() -> list[dict[str, Any]]:
    classes = []
    for key, name, tmin, tmax, production, weapons, medical, electrics in _CLASS_META:
        groups = []
        for gkey, glabel, table in _GROUP_LABELS:
            groups.append({
                "key": gkey,
                "label": glabel,
                "enabled": True,
                "items": _items(table[key], gkey),
            })
        classes.append({
            "key": key,
            "name": name,
            "min_tonnage": tmin,
            "max_tonnage": tmax,
            "enabled": True,
            "requirements": {
                "production": production,
                "weapons": weapons,
                "medical": medical,
                "electrics": electrics,
            },
            "item_groups": groups,
        })
    return classes


DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "formulas": DEFAULT_FORMULAS,
    "equipment": DEFAULT_EQUIPMENT,
    "autofill": DEFAULT_AUTOFILL,
    "forbidden_items": DEFAULT_FORBIDDEN_ITEMS,
    "rules": DEFAULT_RULES,
    "manual_checks": DEFAULT_MANUAL_CHECKS,
    "classes": _build_classes(),
    # Пользовательские правила: выражение над метриками корабля, которое должно
    # быть истинным. Пример: {"id": "my", "label": "Не длиннее 60 м",
    # "expr": "length_m <= 60", "severity": "warning", "enabled": true}
    "custom_rules": [],
    "options": {
        # Проверять ли пристыкованные дроны/челноки (LinkedSubmarine) отдельно.
        "check_drones": True,
        # Наполнение считается по всему кораблю целиком, а не по конкретным
        # шкафам: в .sub предметы лежат плоским списком, привязка к контейнеру
        # есть, но принадлежность шкафа «арсеналу» или «медотсеку» из файла не
        # выводится. Флаг оставлен на будущее и на случай ручного отключения.
        "count_items_globally": True,
    },
}
