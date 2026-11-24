from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.ratings import RatingRepository


def test_rating_upsert_and_report(db, finding):
    FindingRepository(db).save(1, [finding], {}, [])
    r = RatingRepository(db)
    r.save(finding.id, "useful")
    r.save(finding.id, "noise")
    assert r.report() == [{"type": "risky_change", "severity": "high", "useful": 0, "noise": 1}]
