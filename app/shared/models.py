"""app/shared/models.py

كائنات العقد المشتركة بين طبقة المحادثة ومحركي التوصية والتخطيط.
مطابقة لـ docs/contract.md حرفيًا: نفس أسماء الحقول ونفس البنية.
ممنوع تغيير أي حقل هنا دون تعديل docs/contract.md أولًا (قاعدة تغيير العقد).
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# القسم 1: الكائنات المشتركة
# ---------------------------------------------------------------------------

# قاموس الوسوم الموحّد (مغلق — إضافة وسم = تعديل docs/contract.md أولًا)
TAG_VOCAB: set[str] = {
    "tag:historical",
    "tag:religious",
    "tag:nature",
    "tag:sea",
    "tag:market",
    "tag:food",
    "tag:family_fun",
    "tag:adventure",
    "tag:quiet",
    "tag:museum",
}

GroupType = Literal["solo", "couple", "family", "friends", "large_group"]
BudgetLevel = Literal["low", "medium", "high"]
Pace = Literal["relaxed", "moderate", "intense"]
Language = Literal["ar", "en"]


class DateRange(BaseModel):
    start: str
    end: str


class ConversationState(BaseModel):
    """1.1 ConversationState — يُمرَّر مع أغلب الاستدعاءات."""

    destination: list[str] = Field(default_factory=list)
    dates: Optional[DateRange] = None
    duration_days: Optional[int] = None
    budget_level: Optional[BudgetLevel] = None
    group_type: Optional[GroupType] = None
    group_size: Optional[int] = None
    interests: list[str] = Field(default_factory=list)
    pace: Optional[Pace] = None
    excluded_place_ids: list[str] = Field(default_factory=list)
    saved_place_ids: list[str] = Field(default_factory=list)
    current_plan_id: Optional[str] = None
    language: Language = "ar"


class PlaceCard(BaseModel):
    """1.2 PlaceCard — المكان المختصر (لعرض البطاقات)."""

    place_id: str
    name_ar: str
    name_en: str
    city: str
    category: str
    tags: list[str] = Field(default_factory=list)
    rating: float
    reviews_count: int
    photo_url: str
    recommendation_reason: str
    visit_duration_min: int
    lat: float
    lng: float
    price_level: Literal["free", "cheap", "medium", "expensive"]


class ErrorSuggestion(BaseModel):
    action: str
    params: dict = Field(default_factory=dict)


class ErrorDetail(BaseModel):
    type: Literal["no_results", "missing_input", "unsolvable", "internal"]
    user_message: str
    suggestion: Optional[ErrorSuggestion] = None


class ErrorObject(BaseModel):
    """1.3 شكل الخطأ الموحّد."""

    error: ErrorDetail


# ---------------------------------------------------------------------------
# القسم 2: عقد محرك التوصية (5 أدوات)
# ---------------------------------------------------------------------------


class SearchScope(BaseModel):
    city: Optional[str] = None
    category: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    state: ConversationState
    top_k: int = 8
    scope: Optional[SearchScope] = None


class SearchFallback(BaseModel):
    reason: str
    suggestion: ErrorSuggestion


class SearchResponse(BaseModel):
    results: list[PlaceCard]
    ranking_note: str
    fallback: Optional[SearchFallback] = None


class DetailsRequest(BaseModel):
    place_id: str
    language: Language


class OpeningHoursDay(BaseModel):
    open: str
    close: str


class OpeningHours(BaseModel):
    sat: Optional[OpeningHoursDay] = None
    sun: Optional[OpeningHoursDay] = None
    mon: Optional[OpeningHoursDay] = None
    tue: Optional[OpeningHoursDay] = None
    wed: Optional[OpeningHoursDay] = None
    thu: Optional[OpeningHoursDay] = None
    fri: Optional[OpeningHoursDay] = None


class GroupSuitability(BaseModel):
    solo: int
    couple: int
    family: int
    friends: int
    large_group: int


class PlaceDetails(BaseModel):
    place: PlaceCard
    description: str
    opening_hours: OpeningHours
    open_now: bool
    phone: str
    website: str
    photos: list[str] = Field(default_factory=list)
    group_suitability: GroupSuitability
    best_season: str
    best_time_of_day: str
    practical_notes: list[str] = Field(default_factory=list)


class CompareRequest(BaseModel):
    place_ids: list[str]
    state: ConversationState
    language: Language


class CompareAxis(BaseModel):
    axis: str
    label_ar: str
    values: dict[str, Union[float, str]]


class CompareVerdict(BaseModel):
    winner_place_id: str
    reason: str


class CompareResponse(BaseModel):
    places: list[PlaceCard]
    axes: list[CompareAxis]
    verdict: CompareVerdict


LogEventType = Literal[
    "view_card",
    "open_details",
    "like",
    "save",
    "pass",
    "added_to_plan",
    "removed_from_plan",
    "visited",
]
LogSource = Literal["chat", "search", "plan"]


class LogRequest(BaseModel):
    user_id: str
    place_id: str
    event: LogEventType
    source: LogSource
    ts: str


class LogResponse(BaseModel):
    ok: bool = True


class ProfileRequest(BaseModel):
    user_id: str


class LastActivity(BaseModel):
    type: str
    city: str
    days_ago: int


class ProfileResponse(BaseModel):
    top_tags: list[str] = Field(default_factory=list)
    visited_cities: list[str] = Field(default_factory=list)
    usual_group_type: Optional[GroupType] = None
    usual_pace: Optional[Pace] = None
    last_activity: Optional[LastActivity] = None


# ---------------------------------------------------------------------------
# القسم 3: عقد محرك التخطيط (3 أدوات)
# ---------------------------------------------------------------------------


class StartLocation(BaseModel):
    lat: float
    lng: float


class BuildRequest(BaseModel):
    state: ConversationState
    mandatory_place_ids: list[str] = Field(default_factory=list)
    candidate_place_ids: list[str] = Field(default_factory=list)
    start_location: Optional[StartLocation] = None


class CostEstimate(BaseModel):
    activities: float
    food: float
    transport: float
    currency: str = "USD"


StopType = Literal["visit", "meal", "rest", "transfer"]


class PlanStop(BaseModel):
    place_id: Optional[str] = None
    name_ar: str
    name_en: str
    stop_type: StopType
    arrival: str
    departure: str
    visit_duration_min: int
    travel_from_prev_min: int
    travel_mode: Optional[str] = None
    cost_estimate: float
    why_here: str


class PlanDay(BaseModel):
    day_number: int
    date: Optional[str] = None
    title_ar: str
    weather_note: Optional[str] = None
    day_cost_estimate: float
    stops: list[PlanStop]


class TradeoffOption(BaseModel):
    action: str
    label_ar: str
    params: dict = Field(default_factory=dict)


class Tradeoff(BaseModel):
    issue_ar: str
    options: list[TradeoffOption]


class PlanObject(BaseModel):
    """أهم كائن في المشروع — الخطة الكاملة."""

    plan_id: str
    summary_ar: str
    summary_en: str
    total_cost_estimate: CostEstimate
    days: list[PlanDay]
    tradeoffs: list[Tradeoff] = Field(default_factory=list)


ModificationType = Literal[
    "remove",
    "add",
    "replace_with_place",
    "replace_with_kind",
    "move_to_day",
    "shift_time",
    "change_day_pace",
    "extend_days",
    "shrink_days",
]


class Modification(BaseModel):
    type: ModificationType
    target_place_id: Optional[str] = None
    params: dict = Field(default_factory=dict)


class ModifyRequest(BaseModel):
    plan_id: str
    modification: Modification


class PlanChange(BaseModel):
    change_ar: str


class ModifyResponse(BaseModel):
    plan: PlanObject
    changes: list[PlanChange]
    alternatives: list[PlaceCard] = Field(default_factory=list)


class FeasibilityRequest(BaseModel):
    place_ids: list[str]
    duration_days: int
    group_type: Optional[GroupType] = None


class FeasibilitySuggestion(BaseModel):
    action: str
    label_ar: str


class FeasibilityResponse(BaseModel):
    verdict: Literal["comfortable", "tight", "unrealistic"]
    reason_ar: str
    suggestion: Optional[FeasibilitySuggestion] = None


# ---------------------------------------------------------------------------
# ذاكرة العمل والحالة والرد المنظم (spec.md §2.3 - §2.5)
# ---------------------------------------------------------------------------


class RecommendedPlaceRef(BaseModel):
    """مرجع مختصر لمكان ضمن last_recommendations (بترتيبه)."""

    pos: int
    place_id: str
    name_ar: str
    name_en: str


class WorkingMemory(BaseModel):
    last_recommendations: list[RecommendedPlaceRef] = Field(default_factory=list)
    last_mentioned_place: Optional[str] = None
    current_plan: Optional[PlanObject] = None
    last_bot_action: Optional[str] = None
    # عدّادات داخلية لاستراتيجية السؤال (ليست جزءًا من عقد contract.md) —
    # gather_asks: أدوار متتالية **بلا تقدّم** أثناء جمع المعلومات (تهرّب) — سقفها يُنهي الجمع بما توفّر.
    # pending_intent: نية جمع معلّقة ("recommend"/"build_plan") كي يُفهَم الجواب
    #   المقتضب ("لحلب"، "مع عيلتي") تكملةً للمسار لا رسالة غامضة جديدة.
    gather_asks: int = 0
    pending_intent: Optional[str] = None


class ChatRequest(BaseModel):
    session_id: str
    user_id: str
    message: str


# ---------------------------------------------------------------------------
# العقد الخارجي conversation_context_v1 (طبقة المحادثة → Laravel)
# ---------------------------------------------------------------------------
# هذا عقد منفصل تمامًا عن docs/contract.md (الذي يصف طبقة المحادثة ↔ المحركين).
# غرضه: تعيد طبقة المحادثة "فهمًا منظمًا" لكلام المستخدم (نية + قيود + تفضيلات +
# جودة الفهم) بدل قوائم الأماكن؛ ثم Laravel يتحقق ويطبّق القيود ويستدعي البحث
# الدلالي (BGE-M3) ومحرك التوصية. طبقة المحادثة هنا لا تقرر توصية ولا تحسب درجات.

ContextIntent = Literal[
    "recommend_places",
    "search_places",
    "place_details",
    "create_plan",
    "modify_plan",
    "general_question",
]
BudgetTier = Literal["free", "cheap", "medium", "expensive"]
PreferredTime = Literal["morning", "afternoon", "evening", "night"]
Season = Literal["spring", "summer", "autumn", "winter"]
PhysicalDifficulty = Literal["easy", "moderate", "hard"]


class ContextFilters(BaseModel):
    """قيود إلزامية — لا يجوز لـ Laravel إرجاع مكان يخالفها."""

    governorate: Optional[str] = None
    city: Optional[str] = None
    category: Optional[str] = None
    budget_tier: Optional[BudgetTier] = None


class TripContext(BaseModel):
    group_type: Optional[GroupType] = None
    duration_minutes: Optional[int] = None
    preferred_time: Optional[PreferredTime] = None
    season: Optional[Season] = None
    visit_date: Optional[str] = None
    physical_difficulty: Optional[PhysicalDifficulty] = None


class ContextLocation(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_km: Optional[float] = None


class ContextExclusions(BaseModel):
    categories: list[str] = Field(default_factory=list)
    place_ids: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)


class ContextRequest(BaseModel):
    """مدخل نقطة /context — رسالة واحدة، بلا حالة (Laravel يملك حالة المستخدم
    وسلوكه). language اختياري لتجاوز الكشف التلقائي عند الحاجة."""

    message: str
    language: Optional[Language] = None


class ConversationContextV1(BaseModel):
    """المخرج المنظم لفهم رسالة المستخدم (طبقة المحادثة → Laravel)."""

    contract_version: Literal["conversation_context_v1"] = "conversation_context_v1"
    intent: ContextIntent
    language: Language
    query_text: str
    filters: ContextFilters = Field(default_factory=ContextFilters)
    # مفتاح التفضيل → وزن (موجب يرفع الترتيب، سالب يخفضه مثل crowdedness السالب)
    preferences: dict[str, float] = Field(default_factory=dict)
    trip_context: TripContext = Field(default_factory=TripContext)
    location: ContextLocation = Field(default_factory=ContextLocation)
    exclusions: ContextExclusions = Field(default_factory=ContextExclusions)
    confidence: float
    requires_clarification: bool = False
    missing_information: list[str] = Field(default_factory=list)
    clarification_question: Optional[str] = None


class ChatResponse(BaseModel):
    """شكل الجواب — الرد المنظم للواجهة (spec.md §2.5)."""

    reply: str
    cards: Optional[list[PlaceCard]] = None
    plan: Optional[PlanObject] = None
    comparison: Optional[CompareResponse] = None
    state: ConversationState
