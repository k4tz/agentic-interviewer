CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT NOT NULL,
    requirements JSONB NOT NULL DEFAULT '[]'::jsonb,
    company_questions JSONB NOT NULL DEFAULT '[]'::jsonb,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS resumes (
    id UUID PRIMARY KEY,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    raw_bytes BYTEA NOT NULL,
    extracted_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS resumes_sha256_idx ON resumes (sha256);

CREATE TABLE IF NOT EXISTS interview_intakes (
    id UUID PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    resume_id UUID NOT NULL REFERENCES resumes(id),
    interview_id UUID NOT NULL UNIQUE,
    candidate_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO jobs (
    id, slug, title, company, location, description, requirements, company_questions
) VALUES
(
    'job-platform-engineer',
    'senior-platform-engineer',
    'Senior Platform Engineer',
    'Northstar Systems',
    'Remote / India',
    'Design and operate reliable backend platforms used by product teams. The role combines distributed systems design, API ownership, production operations, and technical leadership.',
    '["Strong Python experience and production API design", "Distributed systems fundamentals, queues, caching, and failure handling", "PostgreSQL data modelling and query-performance experience", "Containers, observability, incident response, and capacity planning", "Clear technical communication and evidence-based trade-off decisions"]'::jsonb,
    '["Tell me about a production incident you personally helped resolve and what changed afterward."]'::jsonb
),
(
    'job-ai-application-engineer',
    'ai-application-engineer',
    'AI Application Engineer',
    'Northstar Systems',
    'Bengaluru / Hybrid',
    'Build secure, observable AI product workflows with swappable model providers and well-defined application contracts.',
    '["Python and FastAPI service development", "LLM application evaluation, guardrails, and structured outputs", "Provider-neutral model integration and cost controls", "Async processing, PostgreSQL, and production monitoring", "Practical experience shipping user-facing AI features"]'::jsonb,
    '["Describe an AI system where you deliberately chose a simpler architecture over a more agentic one."]'::jsonb
)
ON CONFLICT (id) DO UPDATE SET
    slug = EXCLUDED.slug,
    title = EXCLUDED.title,
    company = EXCLUDED.company,
    location = EXCLUDED.location,
    description = EXCLUDED.description,
    requirements = EXCLUDED.requirements,
    company_questions = EXCLUDED.company_questions,
    active = TRUE;
