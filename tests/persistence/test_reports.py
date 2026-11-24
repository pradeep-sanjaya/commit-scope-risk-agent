from scope_agent.domain.epic_report import ReportContent, ReportDiff, aggregate
from scope_agent.persistence.reports import ReportRepository


def _c():
    return ReportContent(aggregate=aggregate("PAY-100", [], []), test_scope=[], test_scope_status="ok")


def test_versions_are_append_only(db):
    repo = ReportRepository(db)
    assert repo.latest("PAY-100") is None
    assert repo.append("PAY-100", _c(), ReportDiff(), 3) == 1
    assert repo.append("PAY-100", _c(), ReportDiff(new_commits=2), 5) == 2
    assert repo.append("OTHER", _c(), ReportDiff(), 1) == 1
    assert repo.latest("PAY-100").version == 2 and repo.latest("PAY-100").record_count == 5
    assert [m.version for m in repo.versions("PAY-100")] == [1, 2]
    assert repo.get("PAY-100", 1).record_count == 3
    assert repo.get("PAY-100", 9) is None
    assert repo.versions("PAY-100")[1].diff.new_commits == 2
