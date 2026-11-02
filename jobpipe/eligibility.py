"""Evidence-based application eligibility, independent of canonical fit scoring.

This is a conservative assessment of stated constraints, not certification of
every possible employer rule, continued availability, or hiring suitability.
"""
from __future__ import annotations

from dataclasses import asdict
import re
import math
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalog import load_search_profile
from .experience import extract_experience_requirements
from .models import SearchProfile
from .utils import data_dir, load_yaml

T = TypeVar("T")
EligibilityStatus = Literal["eligible", "ineligible", "needs_review"]


class VerifiedFact(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: T
    verified: bool = False
    evidence: str = Field(min_length=1, max_length=2000)


class Authorization(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    country: str = Field(pattern=r"^[A-Z]{2}$")
    authorized: bool | None = None
    sponsorship_required: bool | None = None
    verified: bool = False
    evidence: str = Field(min_length=1, max_length=2000)


class ApplicantFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    work_authorizations: list[Authorization] = Field(default_factory=list)
    citizenship_countries: VerifiedFact[list[str]] | None = None
    residence_country: VerifiedFact[str] | None = None
    residence_state: VerifiedFact[str] | None = None
    commute_locations: VerifiedFact[list[str]] | None = None
    professional_experience_years: VerifiedFact[float] | None = None
    experience_by_area: dict[str, VerifiedFact[float]] = Field(default_factory=dict)
    enrolled: VerifiedFact[bool] | None = None
    institution_country: VerifiedFact[str] | None = None
    graduation_year: VerifiedFact[int] | None = None
    languages: VerifiedFact[list[str]] | None = None
    native_languages: VerifiedFact[list[str]] | None = None
    language_proficiencies: dict[str, VerifiedFact[str]] = Field(default_factory=dict)
    credentials: VerifiedFact[list[str]] | None = None
    restricted_employers: VerifiedFact[list[str]] | None = None
    allowed_employment_types: VerifiedFact[list[str]] | None = None


    @model_validator(mode="after")
    def consistent_verified_facts(self) -> ApplicantFacts:
        countries = [a.country for a in self.work_authorizations]
        if len(countries) != len(set(countries)):
            raise ValueError("Use one authorization fact per country; resolve conflicting claims first.")
        for fact in [self.professional_experience_years, *self.experience_by_area.values()]:
            if fact is not None and (fact.value < 0 or not math.isfinite(fact.value)):
                raise ValueError("Experience years must be finite and nonnegative.")
        return self


class Check(BaseModel):
    status: EligibilityStatus
    evidence: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    applicant_fact_keys: list[str] = Field(default_factory=list)


class Assessment(BaseModel):
    status: EligibilityStatus
    checks: dict[str, Check]
    evidence: list[str]
    concerns: list[str]
    missing_applicant_information: list[str]
    missing_employer_information: list[str]
    recommended_next_action: str
    location_scope: str
    hiring_countries: list[str]
    experience_requirements: list[dict[str, Any]]
    profile_revision: int = 0
    configuration_fingerprint: str = ""
    scope: str = "Stated posting requirements and verified applicant facts; availability and complete employer policy are not certified."


def _load_legacy_applicant(name: str | None = None) -> tuple[ApplicantFacts, dict[str, Any]]:
    """Resolve registered profile files by name only, never caller-supplied paths."""
    selected = name or "candidate"
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", selected):
        raise ValueError("Applicant profile must be a registered local profile name, not a path.")
    path = data_dir() / "profiles" / f"{selected}.yaml"
    if not path.exists() and name is None:
        return ApplicantFacts(), {}
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise ValueError(f"Applicant profile {selected!r} is missing or unsafe.")
    raw = load_yaml(path)
    if not isinstance(raw, dict):
        raise ValueError("Candidate profile must be a YAML mapping.")
    return ApplicantFacts.model_validate(raw.get("eligibility", {})), raw


def load_applicant(name: str | None = None) -> tuple[ApplicantFacts, dict[str, Any]]:
    from .applicant_profile import eligibility_snapshot, state
    if name in {None, "candidate"} and state()["active"]:
        return eligibility_snapshot()
    return _load_legacy_applicant(name)


COUNTRIES = {
    "US": r"united states|\busa\b",
    "CA": r"\bcanada\b|\btoronto\b|\bvancouver\b|\bmontreal\b",
    "GB": r"united kingdom|\buk\b|\blondon\b|\bengland\b",
    "FR": r"\bfrance\b|\bparis\b",
    "ES": r"\bspain\b|\bmadrid\b|\bbarcelona\b|\bspanish\b",
    "DE": r"\bgermany\b|\bberlin\b|\bmunich\b",
    "IE": r"\bireland\b|\bdublin\b",
    "AU": r"\baustralia\b|\bsydney\b|\bmelbourne\b",
    "BR": r"\bbrazil\b|s[aã]o paulo",
    "IN": r"\bindia\b|\bbangalore\b|\bbengaluru\b",
    "CN": r"\bchina\b|\bbeijing\b|\bshanghai\b",
    "TW": r"\btaiwan\b|\btaipei\b",
    "JP": r"\bjapan\b|\btokyo\b",
    "IT": r"\bitaly\b|\brome\b",
    "MY": r"\bmalaysia\b|\bkuala lumpur\b",
    "TH": r"\bthailand\b|\bbangkok\b",
    "SG": r"\bsingapore\b",
    "KR": r"\bsouth korea\b|\bseoul\b",
    "PT": r"\bportugal\b|\blisbon\b",
    "NL": r"\bnetherlands\b|\bamsterdam\b",
    "PT": r"\bportugal\b|\blisbon\b",
    "PL": r"\bpoland\b|\bwarsaw\b",
    "MX": r"\bmexico\b",
    "NZ": r"new zealand",
    "SG": r"\bsingapore\b",
}
STATES = dict(zip(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split(),
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|District of Columbia".split("|")))


def _countries(text: str) -> set[str]:
    found = {code for code, pattern in COUNTRIES.items() if re.search(pattern, text, re.I)}
    # The pronoun "us" is not a country. Recognize country labels without
    # allowing incidental company prose to authorize a US hiring region.
    if (re.search(r"\bUS\b", text)
            or re.search(r"\bU\.S\.?(?=\W|$)|\bremote[\s–—-]+us\b|\bus-only\b", text, re.I)
            or text.strip().casefold() == "us"):
        found.add("US")
    return found


def _states(text: str) -> set[str]:
    # Two-letter abbreviations are case sensitive; prose "or"/"in" are not states.
    return {code for code, name in STATES.items()
            if re.search(r"\b" + re.escape(name) + r"\b", text, re.I)
            or re.search(r"\b" + code + r"\b", text)}


def _value(fact: VerifiedFact | None) -> Any:
    return fact.value if fact and fact.verified else None


def assess_eligibility(job: Any, applicant: ApplicantFacts,
                       profile: SearchProfile | None = None,
                       candidate: dict[str, Any] | None = None) -> Assessment:
    """Assess a canonical or in-memory discovered job without any writes/network."""
    if not isinstance(job, dict):
        job = job.model_dump()
    profile = profile or load_search_profile()
    candidate = candidate or {}
    description = str(job.get("description") or job.get("raw_text") or "")
    title, location = str(job.get("title") or ""), str(job.get("location") or "")
    missing_applicant: list[str] = []
    missing_employer: list[str] = []
    checks: dict[str, Check] = {}

    def check(name: str, status: EligibilityStatus = "eligible", evidence: list[str] | None = None,
              concern: str | None = None, applicant_gap: str | None = None,
              employer_gap: str | None = None) -> None:
        checks[name] = Check(status=status, evidence=evidence or [], concerns=[concern] if concern else [])
        if applicant_gap:
            missing_applicant.append(applicant_gap)
            keys: list[str] = []
            if name == "location":
                keys = ["residence.country"] if "country" in applicant_gap else ["residence.state"] if "state" in applicant_gap.lower() else ["residence.commute_locations"]
            elif name == "work_authorization":
                keys = ([f"work_authorization.{auth_country}.sponsorship_required"] if "sponsorship requirement" in applicant_gap.lower()
                        else ["qualifications.citizenship_countries"] if "citizenship" in applicant_gap.lower()
                        else [f"work_authorization.{auth_country}.authorized"] if auth_country else [])
            elif name == "experience":
                keys = ["qualifications.professional_experience_years"] if years is None else []
                for area in ("python", "java", "javascript", "sql", "backend", "data engineering", "software engineering", "customer support", "technical support"):
                    if any(re.search(r"\b" + re.escape(area) + r"\b", r.evidence, re.I) for r in mandatory):
                        keys.append(f"qualifications.{area.replace(' ', '_')}.professional_experience_years")
            elif name == "internship":
                keys = ["education.enrolled"]
                if school_countries:
                    keys.append("education.institution_country")
                if grad_years:
                    keys.append("education.graduation_year")
            elif name in {"additional_requirements", "language_proficiency"}:
                from .applicant_profile import _record_id
                text = " ".join(special if name == "additional_requirements" else native).lower()
                for language in ("english", "spanish", "french", "german", "portuguese", "japanese", "italian", "chinese simplified", "chinese traditional"):
                    if language in text:
                        rid = candidate.get("language_record_ids", {}).get(language, _record_id(language))
                        keys.extend([f"languages.{rid}.name", f"languages.{rid}.proficiency"])
                if re.search(r"bachelor|master|degree|certification", text):
                    keys.append("qualifications.credentials")
            checks[name].applicant_fact_keys = list(dict.fromkeys(keys))
        if employer_gap:
            missing_employer.append(employer_gap)

    # Only location/hiring clauses are usable as geographic evidence. A remote
    # benefit or a foreign office named elsewhere cannot authorize US remote work.
    clauses = [s.strip() for s in re.split(r"[\n;]|(?<=[.!?])\s+", description) if s.strip()]
    hiring_clauses = [s for s in clauses if re.search(
        r"(?:this (?:role|position).{0,50}(?:remote|based|hybrid|on.?site)|"
        r"(?:must|need to|required to) (?:be (?:based|located|resident|in\b)|reside|live)|"
        r"(?:remote (?:role|position|work|within|in|from|anywhere|worldwide|[-–]))|"
        r"(?:work remotely (?:from|in|anywhere))|(?:hiring|hire|employ).{0,40}(?:only|countries|states))", s, re.I)]
    title_regions = [v for v in re.findall(r"\(([^)]+)\)", title) if _countries(v)]
    geo = " ".join([location, str(job.get("remote_type") or ""), *title_regions, *hiring_clauses])
    countries = _countries(geo)
    states = _states(geo)
    # A bare CA/IN/etc. can also be a country code. Do not manufacture US
    # geography from abbreviations without country or spelled-out state evidence.
    explicit_state = any(name not in {"Georgia", "Washington"}
                         and re.search(r"\b" + re.escape(name) + r"\b", geo, re.I)
                         for name in STATES.values())
    if states and ("US" in countries or explicit_state):
        countries.add("US")
    # Known US cities only; unknown cities require employer confirmation.
    if re.search(r"\b(?:san francisco|new york city|boston|austin|seattle|pittsburgh)\b", location, re.I):
        countries.add("US")
    remote = bool(re.search(r"\bremote\b|work from home|work remotely", geo, re.I))
    hybrid = bool(re.search(r"\bhybrid\b", geo, re.I))
    onsite = bool(re.search(r"on.?site|in.office", geo, re.I))
    worldwide = bool(re.search(r"worldwide|globally|anywhere in the world", geo, re.I)) or (
        not countries and bool(re.search(r"remote.{0,20}anywhere|work from anywhere", geo, re.I)))
    residence = _value(applicant.residence_country)
    state = _value(applicant.residence_state)
    evidence = [f"Posting location: {location}"] if location else []
    if title_regions:
        evidence.append(f"Posting country-qualified title: {title}")
    evidence.extend(f"Posting: {s}" for s in hiring_clauses)
    if residence:
        evidence.append(f"Applicant residence: {residence}; {applicant.residence_country.evidence}")
    if state:
        evidence.append(f"Applicant state: {state}; {applicant.residence_state.evidence}")
    allowed = _countries(" ".join(profile.preferred_locations + profile.allowed_locations))
    excluded = next((term for term in profile.excluded_locations if term and term.casefold() in location.casefold()), None)
    scope = "ambiguous"
    if worldwide:
        scope = "remote_anywhere"
    elif remote and countries == {"US"}:
        scope = "remote_us_states" if states else "remote_us"
    elif remote and countries:
        scope = "remote_selected_countries"
    elif countries:
        scope = "hybrid" if hybrid else "onsite"
    location_countries = _countries(location)
    if excluded:
        check("location", "ineligible", evidence, f"Posting location conflicts with explicit preference: {excluded}.")
    elif countries and allowed and not worldwide and not countries.intersection(allowed):
        check("location", "ineligible", evidence, "Posting location is outside explicitly allowed/preferred countries.")
    elif location_countries and countries - location_countries and not re.search(r"remote", location + str(job.get("remote_type") or ""), re.I):
        check("location", "needs_review", evidence, "Description hiring region conflicts with the listed location.", employer_gap="Confirm which listed/remote locations apply to this position.")
    elif worldwide and re.search(r"except|excluding|only|restricted", geo, re.I):
        check("location", "needs_review", evidence, "Worldwide wording has geographic restrictions.", employer_gap="Confirm permitted countries despite worldwide wording.")
    elif (_countries(location) and _countries(" ".join(hiring_clauses))
          and not _countries(location).intersection(_countries(" ".join(hiring_clauses)))
          and not worldwide):
        check("location", "needs_review", evidence, "Location field and role-specific hiring region conflict.", employer_gap="Resolve conflicting posting locations and remote hiring regions.")
    elif hybrid and remote and re.search(r"fully remote|100% remote", geo, re.I):
        check("location", "needs_review", evidence, "Conflicting remote/hybrid evidence.", employer_gap="Resolve contradictory workplace requirements.")
    elif remote and not hybrid and not onsite and (worldwide or countries):
        if not residence:
            check("location", "needs_review", evidence, "Residence is unverified.", applicant_gap="Verified country of residence.")
        elif not worldwide and residence not in countries:
            check("location", "ineligible", evidence, "Verified residence is outside the stated remote hiring region.")
        elif states and re.search(r"except|excluding|not (?:in|available)|not eligible", geo, re.I):
            check("location", "needs_review", evidence, "State exclusions need confirmation.", employer_gap="Confirm allowed states, including exclusions.")
        elif states and not state:
            check("location", "needs_review", evidence, "State eligibility is unresolved.", applicant_gap="Verified US state of residence.")
        elif states and str(state).upper() not in states and str(state).casefold() not in {STATES[c].casefold() for c in states}:
            check("location", "ineligible", evidence, "Verified state is outside the stated hiring states.")
        else:
            # Exclusion lists and complex state policies need interpretation.
            if states and re.search(r"except|excluding|not (?:in|available)|not eligible", geo, re.I):
                check("location", "needs_review", evidence, "State exclusions need confirmation.", employer_gap="Confirm allowed states, including exclusions.")
            else:
                check("location", evidence=evidence)
    elif countries and (hybrid or onsite or not remote):
        commute = _value(applicant.commute_locations)
        styles = {v.lower() for v in profile.preferred_work_styles}
        if styles == {"remote"} and (hybrid or onsite):
            check("location", "ineligible", evidence, "Posting requires attendance; profile explicitly targets remote work only.")
        elif commute and any(v.casefold() in location.casefold() for v in commute):
            check("location", evidence=evidence + [f"Applicant commute confirmation: {applicant.commute_locations.evidence}"])
        else:
            check("location", "needs_review", evidence, "Onsite/hybrid attendance or relocation is unverified.", applicant_gap="Verified ability to attend this work location; relocation is not assumed.")
    else:
        check("location", "needs_review", evidence, "No unambiguous hiring region and workplace arrangement.", employer_gap="Explicit work location and permitted remote hiring region.")

    # Authorization concerns refer to the working country, not citizenship inferred
    # from a name or technical skills. Worldwide work still needs local authorization.
    auth_country = residence if worldwide else residence if residence in countries else next(iter(countries)) if len(countries) == 1 else None
    auth = next((a for a in applicant.work_authorizations if a.country == auth_country and a.verified), None)
    auth_clauses = [s for s in clauses if re.search(r"work authori[sz]|right to work|sponsor|citizenship|citizens only|clearance", s, re.I)
                    and not re.search(r"equal opportunity|do not discriminate|without regard|regardless of", s, re.I)]
    auth_text = " ".join(auth_clauses)
    no_sponsorship = bool(re.search(r"no.{0,15}sponsor|(?:cannot|unable|do not|does not|not).{0,25}sponsor|without.{0,15}sponsor", auth_text, re.I))
    auth_evidence = [f"Posting: {s}" for s in auth_clauses]
    if auth:
        auth_evidence.append(f"Applicant {auth.country} authorization: {auth.evidence}")
    restriction_countries = _countries(" ".join(s for s in auth_clauses if re.search(r"must|required|require|only", s, re.I)))
    if restriction_countries and (not auth_country or auth_country not in restriction_countries):
        check("work_authorization", "needs_review", auth_evidence, "Posting authorization restriction conflicts with or exceeds the assessed work region.", employer_gap="Confirm the country whose work authorization is mandatory.")
    elif not auth or auth.authorized is None:
        check("work_authorization", "needs_review", auth_evidence, "Work authorization is unverified.", applicant_gap=f"Verified work authorization for {auth_country or 'the employing country'}.")
    elif auth.authorized is False:
        if auth.sponsorship_required and not no_sponsorship and re.search(r"(?:provide|offer|available).{0,20}sponsorship|sponsorship.{0,20}(?:available|provided)", " ".join(auth_clauses), re.I):
            check("work_authorization", "needs_review", auth_evidence, "Potential sponsorship does not establish authorization.", employer_gap="Confirm individual sponsorship eligibility.")
        else:
            check("work_authorization", "ineligible", auth_evidence, "Applicant is not authorized to work in the required country.")
    elif auth.sponsorship_required is True and re.search(r"no.{0,15}sponsor|(?:cannot|unable|do not|does not|not).{0,25}sponsor|without.{0,15}sponsor", " ".join(auth_clauses), re.I):
        check("work_authorization", "ineligible", auth_evidence, "Employer excludes required sponsorship.")
    elif auth.sponsorship_required is None and re.search(r"sponsor", " ".join(auth_clauses), re.I):
        check("work_authorization", "needs_review", auth_evidence, "Sponsorship requirement is unknown.", applicant_gap="Verified sponsorship requirement.")
    elif re.search(r"clearance", " ".join(auth_clauses), re.I):
        check("work_authorization", "needs_review", auth_evidence, "Citizenship/clearance restriction needs separate verification.", applicant_gap="Verified citizenship/clearance meeting the explicit restriction.")
    elif re.search(r"citizens|citizenship", auth_text, re.I) and auth_country not in (_value(applicant.citizenship_countries) or []):
        citizenship = _value(applicant.citizenship_countries)
        check("work_authorization", "ineligible" if citizenship is not None else "needs_review", auth_evidence,
              "Required citizenship is not established." if citizenship is None else "Verified citizenship does not meet the explicit restriction.",
              applicant_gap="Verified citizenship meeting the explicit restriction." if citizenship is None else None)
    elif candidate.get("authorization_restrictions", {}).get(auth_country):
        check("work_authorization", "needs_review", auth_evidence, "Verified authorization has restrictions requiring employer review.",
              employer_gap="Confirm whether the stated authorization restrictions permit this position.")
    else:
        check("work_authorization", evidence=auth_evidence)

    requirements = extract_experience_requirements(description)
    mandatory = [r for r in requirements if not r.preferred]
    years = _value(applicant.professional_experience_years)
    exp_evidence = [f"Posting: {r.evidence}" for r in requirements]
    if years is not None:
        exp_evidence.append(f"Applicant professional years: {years}; {applicant.professional_experience_years.evidence}")
    from .classification import classify_seniority as title_seniority
    explicit_level, _ = title_seniority(title)
    if explicit_level in profile.excluded_levels:
        check("experience", "ineligible", [f"Posting title: {title}", f"Applicant search profile excludes {explicit_level} roles."], "Explicit title level conflicts with applicant preferences.")
    elif not description.strip():
        check("experience", "needs_review", exp_evidence, "Posting description is missing.", employer_gap="Complete requirements text.")
    # An uncertain upper bound does not erase a proved minimum conflict.
    # Alternatives ("or equivalent") cannot establish such a conflict alone.
    elif years is not None and any(
        (r.minimum is not None and years < r.minimum)
        or (r.maximum is not None and not r.upper_open and r.minimum is None and years > r.maximum)
        for r in mandatory if not re.search(r"\bor\b|equivalent|combined", r.evidence, re.I)
    ):
        check("experience", "ineligible", exp_evidence, "Verified experience does not meet a mandatory bound.")
    elif mandatory and any(r.ambiguous for r in mandatory):
        check("experience", "needs_review", exp_evidence, "Experience range or alternative is ambiguous.", employer_gap="Clarify experience range/alternatives.")
    elif mandatory and years is None:
        check("experience", "needs_review", exp_evidence, "Professional experience is unverified; projects are not employment.", applicant_gap="Verified professional experience years and relevant areas.")
    else:
        area_gap = False
        for r in mandatory:
            for area in ("python", "java", "javascript", "sql", "backend", "data engineering", "software engineering", "customer support", "technical support"):
                if re.search(r"\b" + re.escape(area) + r"\b", r.evidence, re.I):
                    area_years = _value(applicant.experience_by_area.get(area))
                    if area_years is None:
                        area_gap = True
                    elif r.minimum is not None and area_years < r.minimum:
                        check("experience", "ineligible", exp_evidence, f"Verified {area} experience is below the mandatory minimum.")
                    if area_years is not None:
                        exp_evidence.append(f"Applicant {area} years: {area_years}; {applicant.experience_by_area[area].evidence}")
        if "experience" not in checks:
            if area_gap:
                check("experience", "needs_review", exp_evidence, "Total employment years do not establish domain-specific experience.", applicant_gap="Verified years in the explicitly required experience areas.")
            else:
                check("experience", evidence=exp_evidence or ["No mandatory numeric experience requirement identified in the supplied posting; no project/employment equivalence inferred."])

    from .classification import classify_seniority
    if candidate.get("experience_ranges") and checks["experience"].status == "needs_review":
        checks["experience"].concerns.append("Attested experience is a range; exact professional years are not established.")
        missing_employer.append("Review mandatory experience bounds against the attested range; no exact years were invented.")
    level = job.get("canonical_seniority") or classify_seniority(title)[0]
    if level in profile.excluded_levels:
        check("role_level", "ineligible", [f"Explicit role seniority: {level}; title: {title}"],
              f"Role level is explicitly excluded by search profile {profile.name}.")
    employment = str(job.get("employment_type") or "").strip().casefold()
    type_text = " ".join([title, employment, *[s for s in clauses if re.search(r"(?:this (?:role|position)|employment type|unpaid|commission.only|buy.{0,20}equipment)", s, re.I)]])
    dealbreakers = candidate.get("constraints", {}).get("dealbreakers", [])
    blocked = next((v for v in dealbreakers if v.casefold() in type_text.casefold()), None)
    permitted_types = _value(applicant.allowed_employment_types)
    permitted_types = [v.strip().casefold() for v in permitted_types] if permitted_types else None
    restricted = _value(applicant.restricted_employers) or []
    company = str(job.get("company") or "")
    if blocked or company.casefold() in {v.casefold() for v in restricted}:
        check("employment_type", "ineligible", [f"Posting: {type_text}"], f"Explicit applicant restriction: {blocked or company}.")
    elif permitted_types and not employment:
        check("employment_type", "needs_review", [], "Employment type is unspecified.", employer_gap="Employment type matching applicant restrictions.")
    elif permitted_types and employment not in permitted_types:
        check("employment_type", "ineligible", [f"Posting employment type: {employment}"], "Employment type conflicts with verified restrictions.")
    else:
        check("employment_type", evidence=[f"Posting employment type: {employment}" if employment else "No explicit employment-type blocker identified in the supplied posting."])

    intern = bool(re.search(r"\b(?:intern|internship|co-op)\b", title, re.I))
    enrollment_clauses = [s for s in clauses if re.search(r"(?:must|required|require|eligible|currently).{0,60}(?:enroll|university|student|graduat)|(?:enroll|graduat).{0,60}(?:must|required)", s, re.I)]
    if intern or enrollment_clauses:
        evidence = [f"Posting: {s}" for s in enrollment_clauses]
        enrolled = _value(applicant.enrolled)
        school_country = _value(applicant.institution_country)
        school_countries = _countries(" ".join(enrollment_clauses))
        grad = _value(applicant.graduation_year)
        enrollment_text = " ".join(enrollment_clauses)
        grad_years = {int(v) for v in re.findall(r"\b20[2-9]\d\b", enrollment_text)}
        for lo, hi in re.findall(r"(20[2-9]\d)\s*(?:[-–—]|to)\s*(20[2-9]\d)", enrollment_text):
            if 0 <= int(hi) - int(lo) <= 10:
                grad_years.update(range(int(lo), int(hi) + 1))
        if enrolled is False and enrollment_clauses:
            check("internship", "ineligible", evidence + [applicant.enrolled.evidence], "Mandatory student enrollment is not met.")
        elif school_countries and school_country and school_country not in school_countries:
            check("internship", "ineligible", evidence + [applicant.institution_country.evidence], "Required institution country is not met.")
        elif grad_years and grad is not None and grad not in grad_years:
            check("internship", "ineligible", evidence + [applicant.graduation_year.evidence], "Required graduation year is not met.")
        elif not enrollment_clauses or enrolled is None or (school_countries and not school_country) or (grad_years and grad is None):
            check("internship", "needs_review", evidence, "Internship eligibility is unresolved.", applicant_gap="Verified enrollment, institution country and graduation year as applicable.", employer_gap="Complete internship eligibility rules." if not enrollment_clauses else None)
        else:
            check("internship", evidence=evidence + [applicant.enrolled.evidence])

    # Do not silently bless explicit credentials/language restrictions that the
    # numeric experience and location checks cannot establish.
    special = [s for s in clauses if re.search(r"(?:must|required|require|minimum).{0,80}(?:bachelor|master|degree|fluent|fluency|certification)|(?:bachelor|master|degree|fluent|fluency|certification).{0,80}(?:required|mandatory)", s, re.I)]
    if special:
        special_evidence = [f"Posting: {s}" for s in special]
        languages = _value(applicant.languages)
        credentials = _value(applicant.credentials)
        unresolved = False
        mismatch = False
        for clause in special:
            if re.search(r"\bor\b|equivalent|preferred", clause, re.I):
                unresolved = True
                continue
            required_languages = {v for v in ("english", "spanish", "french", "german", "portuguese", "japanese")
                                  if re.search(r"\b" + v + r"\b", clause, re.I)}
            if required_languages and re.search(r"fluent|fluency|language", clause, re.I):
                # An explicitly confirmed lower level is a stated mandatory
                # mismatch; an absent language remains unknown, not negative.
                mismatch |= any(_value(applicant.language_proficiencies.get(language)) in {"basic", "limited_working", "professional_working"} for language in required_languages)
                if languages is None:
                    unresolved = True
                else:
                    # A list of known languages is not an exhaustive negative
                    # claim about every unlisted language.
                    unresolved |= not required_languages.issubset({v.casefold() for v in languages})
                    special_evidence.append(f"Applicant languages: {applicant.languages.evidence}")
            else:
                required_credentials = [v for v in ("bachelor", "master", "certification") if v in clause.casefold()]
                if not required_credentials or credentials is None:
                    unresolved = True
                else:
                    # A broad certification mention does not identify which credential.
                    if "certification" in required_credentials or re.search(r"\bin\b", clause, re.I):
                        unresolved = True
                    else:
                        unresolved |= any(not any(v in c.casefold() for c in credentials) for v in required_credentials)
                    special_evidence.append(f"Applicant credentials: {applicant.credentials.evidence}")
        if mismatch:
            check("additional_requirements", "ineligible", special_evidence, "Verified applicant facts do not meet an explicit language/credential requirement.")
        elif unresolved:
            check("additional_requirements", "needs_review", special_evidence, "Explicit education, language or credential requirement needs verification.", applicant_gap="Verified facts matching the quoted education/language/credential requirements.")
        else:
            check("additional_requirements", evidence=special_evidence)
    if re.search(r"\bfuture (?:roles?|opportunities)|talent (?:pool|community)|expression of interest|general application", title, re.I):
        check("posting_availability", "needs_review", [f"Posting title: {title}"],
              "This is a future-opportunity/talent-pool posting, not a confirmed current vacancy.",
              employer_gap="Confirm a current vacancy before asserting application readiness.")
    native = [s for s in clauses if re.search(r"native.level fluency|native (?:speaker|fluency)", s, re.I)
              and not re.search(r"preferred|nice.to.have", s, re.I)]
    if native:
        native_text = " ".join(native).casefold()
        required = {v for v in ("chinese simplified", "chinese traditional", "english", "spanish", "french", "german", "portuguese", "japanese", "italian") if v in native_text}
        verified_native = {v.casefold() for v in (_value(applicant.native_languages) or [])}
        native_evidence = [f"Posting: {s}" for s in native]
        if required and required.issubset(verified_native):
            check("language_proficiency", evidence=native_evidence + [f"Applicant native proficiency: {applicant.native_languages.evidence}"])
        else:
            check("language_proficiency", "needs_review", native_evidence,
                  "Native-level language proficiency requires explicit verification.",
                  applicant_gap="Verified proficiency at the explicitly required language level.")
    # Relevant contradictory proposals withhold the affected conclusion, while
    # preserving the accepted value in history and unrelated proved blockers.
    for key in candidate.get("conflicting_keys", []):
        section = key.split(".")[0]
        affected = {"residence": "location", "work_authorization": "work_authorization", "education": "internship", "languages": "additional_requirements"}.get(section)
        if key.startswith("qualifications."):
            affected = "experience" if "experience" in key else "additional_requirements"
        if key.startswith("preferences."):
            affected = "employment_type"
        if affected in checks and checks[affected].status == "eligible":
            checks[affected].status = "needs_review"
            checks[affected].concerns.append("Conflicting applicant information requires local review.")
            checks[affected].applicant_fact_keys.append(key)
            missing_applicant.append("Resolve conflicting applicant information: " + key)
    statuses = [v.status for v in checks.values()]
    status: EligibilityStatus = "ineligible" if "ineligible" in statuses else "needs_review" if "needs_review" in statuses else "eligible"
    import hashlib
    import json
    return Assessment(status=status, checks=checks,
        profile_revision=candidate.get("profile_revision", 0),
        configuration_fingerprint=hashlib.sha256(json.dumps(profile.model_dump(), sort_keys=True).encode()).hexdigest(),
        evidence=list(dict.fromkeys(e for c in checks.values() for e in c.evidence)),
        concerns=list(dict.fromkeys(e for c in checks.values() for e in c.concerns)),
        missing_applicant_information=list(dict.fromkeys(missing_applicant)),
        missing_employer_information=list(dict.fromkeys(missing_employer)),
        recommended_next_action={"eligible": "Compare canonical fit and verify current posting before saving/preparing.", "ineligible": "Exclude from the ready-to-apply shortlist; review the cited conflict before reconsidering.", "needs_review": "Resolve the listed applicant/employer evidence gaps before asserting readiness."}[status],
        location_scope=scope, hiring_countries=sorted(countries), experience_requirements=[asdict(r) for r in requirements])
