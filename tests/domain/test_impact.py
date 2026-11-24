from datetime import datetime, timezone

from scope_agent.domain.areas import AreaMap, AreaRule
from scope_agent.domain.impact import Endpoint, build_impact_record, endpoint_changes, is_config, is_migration
from scope_agent.domain.models import (
    CommitData, DependencyChange, FileChange, Finding, FindingType, Severity, TicketContext,
)

WHEN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fc(path, before, after):
    return FileChange(path=path, status="modified", before=before, after=after)


def test_spring_endpoint_changes():
    before = '@RestController\n@RequestMapping("/api")\nclass C {\n @GetMapping("/cart") void a(){}\n}'
    after = ('@RestController\n@RequestMapping("/api")\nclass C {\n @GetMapping("/cart") void a(){}\n'
             ' @PostMapping("/checkout") void b(){}\n}')
    assert endpoint_changes(fc("src/C.java", before, after)) == [Endpoint("POST", "/api/checkout", "added", "src/C.java")]


def test_spring_annotation_forms_and_request_mapping_methods():
    after = (
        '@RequestMapping(path = "/v1/")\nclass C {\n'
        ' @GetMapping(value = {"/a", "/b"}) void a(){}\n'
        ' @RequestMapping(value="/c", method = RequestMethod.PUT) void c(){}\n'
        ' @RequestMapping("/d") void d(){}\n'
        ' @DeleteMapping(path="e") void e(){}\n}'
    )
    got = {(e.method, e.path) for e in endpoint_changes(fc("C.java", None, after))}
    assert got == {("GET", "/v1/a"), ("GET", "/v1/b"), ("PUT", "/v1/c"), ("ANY", "/v1/d"), ("DELETE", "/v1/e")}


def test_spring_request_mapping_method_set_and_removed_and_changed():
    before = ('class C {\n @RequestMapping(value="/x", method = {RequestMethod.GET}) void a(){}\n'
              ' @GetMapping("/y") void b(){}\n @GetMapping("/z") void z(){}\n}')
    after = 'class C {\n @GetMapping("/y", produces="json") void b(){}\n @GetMapping("/z") void z(){}\n}'
    got = endpoint_changes(fc("C.java", before, after))
    assert Endpoint("GET", "/x", "removed", "C.java") in got
    assert Endpoint("GET", "/y", "changed", "C.java") in got
    assert len(got) == 2


def test_spring_class_word_in_header_comment_does_not_move_class_boundary():
    after = (
        '/* Copyright: this class is licensed under ... */\n'
        'package p;\n'
        '/** Controller class for payments. */\n'
        '@RestController\n@RequestMapping("/api/pay") // the class prefix\n'
        'public class PayController {\n'
        ' // @GetMapping("/commented-out") void old(){}\n'
        ' @GetMapping("/status") void s(){}\n'
        ' @PostMapping("/charge") void c(){ String s = "/* not a comment */ class"; }\n}'
    )
    got = {(e.method, e.path) for e in endpoint_changes(fc("PayController.java", None, after))}
    assert got == {("GET", "/api/pay/status"), ("POST", "/api/pay/charge")}


def test_spring_interface_endpoints():
    after = (
        '@FeignClient(name = "ledger")\n@RequestMapping("/ledger")\n'
        'public interface LedgerClient {\n'
        '  @GetMapping("/entries/{id}") Entry get(@PathVariable String id);\n'
        '  @PostMapping("/entries") Entry add(Entry e);\n}'
    )
    got = {(e.method, e.path) for e in endpoint_changes(fc("LedgerClient.java", None, after))}
    assert got == {("GET", "/ledger/entries/{id}"), ("POST", "/ledger/entries")}


def test_angular_route_removed_added_changed():
    p = "web/src/app/app-routing.module.ts"
    assert endpoint_changes(fc(p, "{ path: 'settings' }", "")) == [Endpoint("ROUTE", "settings", "removed", p)]
    assert endpoint_changes(fc("a/x.routes.ts", None, "{ path: 'home', component: A }")) == [
        Endpoint("ROUTE", "home", "added", "a/x.routes.ts")]
    assert endpoint_changes(fc(p, "{ path: 'h', component: A }", "{ path: 'h', component: B }")) == [
        Endpoint("ROUTE", "h", "changed", p)]
    assert endpoint_changes(fc("a/other.ts", None, "{ path: 'home' }")) == []


def test_is_migration():
    assert is_migration("src/main/resources/db/migration/V42__add_col.sql")
    assert is_migration("db/migration/V1__x.sql")
    assert is_migration("src/main/resources/db/changelog/2026/a.xml")
    assert is_migration("svc/liquibase/master.yml")
    assert not is_migration("src/main/resources/db/migration/R__repeat.sql")
    assert not is_migration("src/Main.java")


def test_is_config():
    assert is_config("svc/src/main/resources/application.yml", ["**/application*.yml"])
    assert not is_config("svc/Main.java", ["**/application*.yml"])


def _commit(files, repo="PAY/payments-service"):
    return CommitData(repo=repo, sha="c1", author_name="A", author_email="a@x.test", message="m",
                      parents=["p"], files=files, committed_at=WHEN)


def _ticket(key, epic_key):
    return TicketContext(key=key, summary="s", description="d", issue_type="Task", status="x",
                         acceptance_criteria=None, components=[], labels=[], epic_key=epic_key,
                         epic_summary=None, epic_description=None, linked_titles=[])


def _finding(sev):
    return Finding(repo="PAY/payments-service", commit="c1", author_email=None, jira_keys=[],
                   type=FindingType.ticket_gap, category="c", severity=sev, title="t", explanation="e",
                   evidence=[], files=[], suggested_reviewers=[])


def build(keys, tickets, files=(), findings=(), deps=(), commit=None):
    return build_impact_record(
        commit or _commit(list(files)), keys, tickets, list(deps), list(findings), ["note"],
        AreaMap(rules=[AreaRule(repo="*", glob="src/pay/*", area="Payments")]),
        ["**/application*.yml"], WHEN,
    )


def test_subject_epic_then_ticket_then_unassigned():
    assert build(["PAY-101"], {"PAY-101": _ticket("PAY-101", "PAY-100")}).subject == "PAY-100"
    assert build(["PAY-9"], {"PAY-9": _ticket("PAY-9", None)}).subject == "PAY-9"
    assert build(["PAY-9"], {"PAY-9": None}).subject == "PAY-9"
    r = build([], {})
    assert r.subject == "unassigned/PAY/payments-service" and r.epic_key is None


def test_record_areas_migrations_config_and_max_severity():
    files = [
        fc("src/pay/A.java", "a", "b"),
        fc("src/pay/B.java", "a", "b"),
        fc("db/migration/V42__x.sql", None, "create"),
        fc("svc/application.yml", "a", "b"),
    ]
    dep = DependencyChange(ecosystem="maven", name="g:a", old_version="1", new_version="2", kind="upgraded", file="package.json")
    r = build(["PAY-1"], {"PAY-1": _ticket("PAY-1", "PAY-0")}, files,
              [_finding(Severity.low), _finding(Severity.high)], [dep])
    assert r.areas == ["Payments", "payments-service: db", "payments-service: svc"]
    assert r.migrations == ["db/migration/V42__x.sql"]
    assert r.config_files == ["svc/application.yml"]
    assert r.max_severity == Severity.high
    assert r.dependency_changes == [dep] and len(r.finding_ids) == 2
    assert r.test_note == ["note"] and r.committed_at == WHEN and r.epic_key == "PAY-0"
    assert build([], {}).max_severity is None
