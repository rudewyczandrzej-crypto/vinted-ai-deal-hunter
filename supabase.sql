create table if not exists users (
  id bigint primary key generated always as identity,
  telegram_id text unique not null,
  created_at timestamp with time zone default now()
);

create table if not exists searches (
  id bigint primary key generated always as identity,
  telegram_id text not null,
  keyword text not null,
  max_price numeric,
  country text default 'pl',
  active boolean default true,
  created_at timestamp with time zone default now()
);

create table if not exists sent_items (
  id bigint primary key generated always as identity,
  telegram_id text not null,
  search_id bigint,
  item_id text not null,
  url text,
  sent_at timestamp with time zone default now(),
  unique(telegram_id, item_id)
);

create index if not exists idx_searches_telegram_active on searches(telegram_id, active);
create index if not exists idx_sent_items_telegram_item on sent_items(telegram_id, item_id);
