```sql
create table pm_pipeline.audit_events (
  id bigserial not null,
  created_at timestamp with time zone not null default now(),
  run_id uuid null,
  entity_type text null,
  entity_id uuid null,
  event text null,
  meta jsonb not null default '{}'::jsonb,
  constraint audit_events_pkey primary key (id)
) TABLESPACE pg_default;
```

```sql
create table pm_pipeline.company_candidates (
  id uuid not null default gen_random_uuid (),
  run_id uuid not null,
  name text not null,
  website text not null,
  domain text not null,
  state character(2) not null,
  description text null,
  discovery_source text null,
  pms_detected text null,
  units_estimate integer null,
  company_type text not null default 'sfr'::text,
  evidence jsonb not null default '[]'::jsonb,
  status text not null default 'candidate'::text,
  rejected_reasons jsonb not null default '[]'::jsonb,
  meets_all_requirements boolean not null default false,
  idem_key text null,
  worker_id text null,
  worker_lease_until timestamp with time zone null,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  constraint company_candidates_pkey primary key (id),
  constraint company_candidates_run_id_fkey foreign KEY (run_id) references pm_pipeline.runs (id) on delete CASCADE
) TABLESPACE pg_default;

create unique INDEX IF not exists uq_cc_run_domain on pm_pipeline.company_candidates using btree (run_id, lower(domain)) TABLESPACE pg_default;

create unique INDEX IF not exists uq_cc_idem on pm_pipeline.company_candidates using btree (run_id, lower(COALESCE(idem_key, ''::text))) TABLESPACE pg_default;

create index IF not exists ix_cc_run_status on pm_pipeline.company_candidates using btree (run_id, status) TABLESPACE pg_default;

create index IF not exists ix_cc_lease on pm_pipeline.company_candidates using btree (run_id, worker_lease_until) TABLESPACE pg_default;
```

```sql
create table pm_pipeline.company_research (
  id uuid not null default gen_random_uuid (),
  run_id uuid not null,
  company_id uuid not null,
  facts jsonb not null default '{}'::jsonb,
  signals jsonb not null default '{}'::jsonb,
  confidence numeric null default 0,
  status text not null default 'complete'::text,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  constraint company_research_pkey primary key (id),
  constraint company_research_run_id_company_id_key unique (run_id, company_id),
  constraint company_research_run_id_fkey foreign KEY (run_id) references pm_pipeline.runs (id) on delete CASCADE,
  constraint company_research_company_id_fkey foreign KEY (company_id) references pm_pipeline.company_candidates (id) on delete CASCADE
) TABLESPACE pg_default;
```

```sql
create table pm_pipeline.contact_candidates (
  id uuid not null default gen_random_uuid (),
  run_id uuid not null,
  company_id uuid not null,
  full_name text not null,
  title text null,
  email text null,
  linkedin_url text null,
  department text null,
  seniority text null,
  quality_score numeric null default 0,
  signals jsonb not null default '{}'::jsonb,
  evidence jsonb not null default '[]'::jsonb,
  status text not null default 'candidate'::text,
  rejected_reasons jsonb not null default '[]'::jsonb,
  meets_all_requirements boolean not null default false,
  idem_key text null,
  worker_id text null,
  worker_lease_until timestamp with time zone null,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  constraint contact_candidates_pkey primary key (id),
  constraint contact_candidates_run_id_fkey foreign KEY (run_id) references pm_pipeline.runs (id) on delete CASCADE,
  constraint contact_candidates_company_id_fkey foreign KEY (company_id) references pm_pipeline.company_candidates (id) on delete CASCADE
) TABLESPACE pg_default;

create unique INDEX IF not exists uq_ct_idem on pm_pipeline.contact_candidates using btree (
  run_id,
  company_id,
  lower(COALESCE(idem_key, ''::text))
) TABLESPACE pg_default;

create index IF not exists ix_ct_run_status on pm_pipeline.contact_candidates using btree (run_id, status) TABLESPACE pg_default;

create index IF not exists ix_ct_lease on pm_pipeline.contact_candidates using btree (run_id, worker_lease_until) TABLESPACE pg_default;

create unique INDEX IF not exists uq_ct_email_per_company on pm_pipeline.contact_candidates using btree (run_id, company_id, lower(email)) TABLESPACE pg_default
where
  (
    (email is not null)
    and (email <> ''::text)
  );

create unique INDEX IF not exists uq_ct_linkedin_per_company on pm_pipeline.contact_candidates using btree (run_id, company_id, lower(linkedin_url)) TABLESPACE pg_default
where
  (
    (linkedin_url is not null)
    and (linkedin_url <> ''::text)
  );
```

```sql
create table pm_pipeline.runs (
  id uuid not null default gen_random_uuid (),
  created_at timestamp with time zone not null default now(),
  created_by uuid null,
  criteria jsonb not null,
  target_quantity integer not null,
  contacts_min integer not null default 1,
  contacts_max integer not null default 3,
  target_distribution jsonb null,
  stage text not null default 'company_discovery'::text,
  status text not null default 'active'::text,
  notes text null,
  constraint runs_pkey primary key (id)
) TABLESPACE pg_default;
```

```sql
create table pm_pipeline.hubspot_domain_suppression (
  domain text not null,
  is_customer boolean not null default false,
  last_contacted timestamp with time zone null,
  last_checked_at timestamp with time zone not null default now(),
  raw_hubspot jsonb not null default '{}'::jsonb,
  constraint hubspot_domain_suppression_pkey primary key (domain)
) TABLESPACE pg_default;
```

```sql
create table pm_pipeline.suppression_domains (
  domain text not null,
  constraint suppression_domains_pkey primary key (domain)
) TABLESPACE pg_default;
```

```sql
create view pm_pipeline.v_blocked_domains as
select
  v_internal_customer_domains.domain
from
  pm_pipeline.v_internal_customer_domains
union
select
  v_internal_recent_contact_domains.domain
from
  pm_pipeline.v_internal_recent_contact_domains
union
select distinct
  lower(hubspot_domain_suppression.domain) as domain
from
  pm_pipeline.hubspot_domain_suppression
where
  hubspot_domain_suppression.is_customer = true
  or hubspot_domain_suppression.last_contacted is not null
  and hubspot_domain_suppression.last_contacted >= (now() - '90 days'::interval)
union
select
  lower(suppression_domains.domain) as domain
from
  pm_pipeline.suppression_domains;
```

```sql
create view pm_pipeline.v_company_contact_counts as
select
  c.run_id,
  c.id as company_id,
  count(ct.*) filter (
    where
      ct.status = any (array['validated'::text, 'promoted'::text])
  ) as contacts_ready
from
  pm_pipeline.company_candidates c
  left join pm_pipeline.contact_candidates ct on ct.company_id = c.id
group by
  c.run_id,
  c.id;
```

```sql
create view pm_pipeline.v_company_gap as
select
  r.id as run_id,
  r.target_quantity,
  count(c.*) filter (
    where
      c.status = any (array['validated'::text, 'promoted'::text])
  ) as companies_ready,
  GREATEST(
    r.target_quantity - count(c.*) filter (
      where
        c.status = any (array['validated'::text, 'promoted'::text])
    ),
    0::bigint
  ) as companies_gap
from
  pm_pipeline.runs r
  left join pm_pipeline.company_candidates c on c.run_id = r.id
group by
  r.id,
  r.target_quantity;
```

```sql
create view pm_pipeline.v_company_research_queue as
select
  c.id as company_id,
  c.run_id,
  c.domain,
  c.name,
  c.website,
  c.state
from
  pm_pipeline.company_candidates c
  left join pm_pipeline.company_research r on r.company_id = c.id
where
  (
    c.status = any (array['validated'::text, 'promoted'::text])
  )
  and r.id is null;
```

```sql
create view pm_pipeline.v_company_state_gap as
with
  dist as (
    select
      runs.id as run_id,
      runs.target_distribution
    from
      pm_pipeline.runs
    where
      runs.target_distribution is not null
  ),
  counts as (
    select
      company_candidates.run_id,
      company_candidates.state,
      count(*) as ready
    from
      pm_pipeline.company_candidates
    where
      company_candidates.status = any (array['validated'::text, 'promoted'::text])
    group by
      company_candidates.run_id,
      company_candidates.state
  )
select
  r.id as run_id,
  s.key as state,
  s.value::integer as target_for_state,
  COALESCE(c.ready, 0::bigint) as ready_for_state,
  GREATEST(
    s.value::integer - COALESCE(c.ready, 0::bigint),
    0::bigint
  ) as gap_for_state
from
  pm_pipeline.runs r
  join dist d on d.run_id = r.id
  join lateral jsonb_each(d.target_distribution) s (key, value) on true
  left join counts c on c.run_id = r.id
  and c.state::text = s.key;
```

```sql
create view pm_pipeline.v_contact_discovery_queue as
select
  c.id as company_id,
  c.run_id,
  c.domain,
  c.name,
  c.state
from
  pm_pipeline.company_candidates c
where
  c.status = any (array['validated'::text, 'promoted'::text]);
```

```sql
create view pm_pipeline.v_contact_gap as
with
  per_company as (
    select
      v_contact_gap_per_company.run_id,
      v_contact_gap_per_company.company_id,
      v_contact_gap_per_company.domain,
      v_contact_gap_per_company.contacts_min,
      v_contact_gap_per_company.contacts_max,
      v_contact_gap_per_company.contacts_ready,
      v_contact_gap_per_company.contacts_min_gap,
      v_contact_gap_per_company.contacts_capacity
    from
      pm_pipeline.v_contact_gap_per_company
  ),
  agg as (
    select
      per_company.run_id,
      sum(per_company.contacts_min_gap) as contacts_min_gap_total,
      sum(per_company.contacts_capacity) as contacts_capacity_total
    from
      per_company
    group by
      per_company.run_id
  )
select
  r.id as run_id,
  r.contacts_min,
  r.contacts_max,
  COALESCE(a.contacts_min_gap_total, 0::numeric) as contacts_min_gap_total,
  COALESCE(a.contacts_capacity_total, 0::numeric) as contacts_capacity_total
from
  pm_pipeline.runs r
  left join agg a on a.run_id = r.id;
```

```sql
create view pm_pipeline.v_contact_gap_per_company as
select
  r.id as run_id,
  c.id as company_id,
  c.domain,
  r.contacts_min,
  r.contacts_max,
  COALESCE(cc.contacts_ready, 0::bigint) as contacts_ready,
  GREATEST(
    r.contacts_min - COALESCE(cc.contacts_ready, 0::bigint),
    0::bigint
  ) as contacts_min_gap,
  GREATEST(
    r.contacts_max - COALESCE(cc.contacts_ready, 0::bigint),
    0::bigint
  ) as contacts_capacity
from
  pm_pipeline.company_candidates c
  join pm_pipeline.runs r on r.id = c.run_id
  left join pm_pipeline.v_company_contact_counts cc on cc.company_id = c.id
where
  c.status = any (array['validated'::text, 'promoted'::text]);
```

```sql
create view pm_pipeline.v_enrichment_requests_actionable as
select
  er.id as request_id,
  er.request_time,
  er.workflow_status,
  er.workflow_run_id,
  er.request as raw_request,
  COALESCE((er.request ->> 'quantity'::text)::integer, 30) as target_quantity,
  GREATEST(
    COALESCE((er.request ->> 'contacts_min'::text)::integer, 1),
    0
  ) as contacts_min,
  GREATEST(
    COALESCE((er.request ->> 'contacts_max'::text)::integer, 3),
    1
  ) as contacts_max,
  COALESCE(er.request -> 'criteria'::text, '{}'::jsonb) as criteria,
  COALESCE(
    er.request -> 'target_distribution'::text,
    null::jsonb
  ) as target_distribution,
  COALESCE(
    er.request -> 'suppression_list'::text,
    '[]'::jsonb
  ) as suppression_list
from
  enrichment_requests er
where
  er.workflow_status = any (array['pending'::text, 'processing'::text]);
```

```sql
create view pm_pipeline.v_internal_customer_domains as
select distinct
  lower(customer_database.domain) as domain
from
  customer_database
where
  customer_database.domain is not null
  and customer_database.lifecycle_stage = 'customer'::text
  and customer_database.churn_date is null;
```
```sql
create view pm_pipeline.v_internal_recent_contact_domains as
select distinct
  lower(research_database.domain) as domain
from
  research_database
where
  research_database.domain is not null
  and GREATEST(
    COALESCE(
      research_database.hubspot_last_contacted,
      '-infinity'::timestamp with time zone
    ),
    COALESCE(
      research_database.hubspot_last_engagement,
      '-infinity'::timestamp with time zone
    )
  ) >= (now() - '90 days'::interval);
```

```sql
create view pm_pipeline.v_request_companies_needing_contacts as
select
  er.id as request_id,
  c.run_id,
  c.id as company_id,
  c.domain,
  c.name,
  c.state,
  cgpc.contacts_ready,
  cgpc.contacts_min_gap,
  cgpc.contacts_capacity
from
  enrichment_requests er
  join pm_pipeline.runs r on er.workflow_run_id ~ '^[0-9a-fA-F-]{36}$'::text
  and r.id = er.workflow_run_id::uuid
  join pm_pipeline.v_contact_gap_per_company cgpc on cgpc.run_id = r.id
  join pm_pipeline.company_candidates c on c.id = cgpc.company_id
where
  cgpc.contacts_min_gap > 0
order by
  cgpc.contacts_min_gap desc,
  cgpc.contacts_capacity desc;
```


```sql
create view pm_pipeline.v_request_progress as
select
  er.id as request_id,
  er.workflow_status,
  er.workflow_run_id,
  r.id as run_id,
  r.stage as run_stage,
  r.status as run_status,
  COALESCE(cg.target_quantity, r.target_quantity) as target_quantity,
  COALESCE(cg.companies_ready, 0::bigint) as companies_ready,
  COALESCE(cg.companies_gap, 0::bigint) as companies_gap,
  COALESCE(ctg.contacts_min_gap_total, 0::numeric) as contacts_min_gap_total,
  COALESCE(ctg.contacts_capacity_total, 0::numeric) as contacts_capacity_total
from
  enrichment_requests er
  join pm_pipeline.runs r on er.workflow_run_id ~ '^[0-9a-fA-F-]{36}$'::text
  and r.id = er.workflow_run_id::uuid
  left join pm_pipeline.v_company_gap cg on cg.run_id = r.id
  left join pm_pipeline.v_contact_gap ctg on ctg.run_id = r.id;
```

```sql
create view pm_pipeline.v_request_resume_plan as
select
  er.id as request_id,
  er.workflow_status,
  r.id as run_id,
  vrp.stage as run_stage,
  vrp.status as run_status,
  vrp.target_quantity,
  vrp.companies_ready,
  vrp.companies_gap,
  vrp.contacts_min_gap_total,
  vrp.contacts_capacity_total
from
  enrichment_requests er
  join pm_pipeline.runs r on er.workflow_run_id ~ '^[0-9a-fA-F-]{36}$'::text
  and r.id = er.workflow_run_id::uuid
  join pm_pipeline.v_run_resume_plan vrp on vrp.run_id = r.id;
```

```sql
create view pm_pipeline.v_request_runs as
select
  er.id as request_id,
  er.workflow_status,
  er.workflow_run_id,
  r.id as run_id,
  r.created_at as run_created_at,
  r.stage as run_stage,
  r.status as run_status,
  r.target_quantity,
  r.contacts_min,
  r.contacts_max,
  r.criteria,
  r.target_distribution
from
  enrichment_requests er
  join pm_pipeline.runs r on er.workflow_run_id ~ '^[0-9a-fA-F-]{36}$'::text
  and r.id = er.workflow_run_id::uuid;
```


```sql
create view pm_pipeline.v_run_resume_plan as
select
  r.id as run_id,
  r.stage,
  r.status,
  r.target_quantity,
  COALESCE(cg.companies_ready, 0::bigint) as companies_ready,
  COALESCE(cg.companies_gap, 0::bigint) as companies_gap,
  COALESCE(ctg.contacts_min_gap_total, 0::numeric) as contacts_min_gap_total,
  COALESCE(ctg.contacts_capacity_total, 0::numeric) as contacts_capacity_total
from
  pm_pipeline.runs r
  left join pm_pipeline.v_company_gap cg on cg.run_id = r.id
  left join pm_pipeline.v_contact_gap ctg on ctg.run_id = r.id;
```
```sql
create table pm_pipeline.search_runs (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  status text not null default 'pending',
  location_city text,
  location_state text,
  pms_requirement text,
  units_min integer,
  required_quantity integer,
  oversample_factor numeric not null default 1.5,
  notes text
);
```
```sql
create table pm_pipeline.search_tasks (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references pm_pipeline.search_runs(id),
  source text not null,          -- 'google', 'maps', 'narpm', 'yelp', 'buildium_dir', ...
  geo_slice text not null,       -- 'Nashville, TN', '37203', 'Brentwood, TN', etc.
  pms_alias text not null,       -- 'Buildium', 'property management software Buildium'
  query_template text not null,  -- full query string used
  status text not null default 'pending', -- pending/running/done/failed
  result_count integer,
  unique_hash text not null,     -- dedupe strategies
  constraint uq_task_unique_hash unique (run_id, unique_hash)
);

```
