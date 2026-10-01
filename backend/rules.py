"""冷链探头读数判定：摄氏温度不超过 8 为合格，否则超温。"""

# 候审队列的排序：加急（冷媒车道）在前，组内按编号从小到大。
QUEUE_ORDER_SQL = "is_rush DESC, id ASC"

# “下一辆（可认领）”的唯一选取表达式，认领接口与车道预告必须共用这一段，
# 保证“预告的下一辆”与“真正能认领的下一辆”永不漂移。
# 规则：先清加急再碰普通——只要还有加急单在候审或处理中，普通道闸口关闭，
# 普通单不会成为下一辆；加急道内按编号从小到大放行。
NEXT_HEAD_SQL = f"""
    SELECT id
    FROM probe_readings
    WHERE status = 'pending'
      AND (
            is_rush
            OR NOT EXISTS (
                SELECT 1 FROM probe_readings g
                WHERE g.is_rush AND g.status IN ('pending', 'processing')
            )
      )
    ORDER BY {QUEUE_ORDER_SQL}
    LIMIT 1
"""


def judge_temp(temp_c: float) -> tuple[str, str]:
    if temp_c <= 8:
        return "合格", "探头温度未超过 8℃ 上限"
    return "超温", "探头温度超过 8℃ 冷链上限"


def verdict_for_display(verdict: str | None, status: str) -> str:
    if verdict:
        return verdict
    if status == "pending":
        return "候审"
    if status == "processing":
        return "处理中"
    return "—"
