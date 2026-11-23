-- Read-only role for Bookr Analyst's sync job.
-- Run as the database owner (neondb_owner) in the Neon SQL editor — on the TEST branch first,
-- then on production (ideally pointing the sync at a read replica).
-- Easiest: create the role in the Neon console (Roles → New role) so Neon generates the password,
-- then run only the GRANT / ALTER statements below.

-- CREATE ROLE bookr_analyst_ro WITH LOGIN PASSWORD '<generate-a-strong-password>';

GRANT CONNECT ON DATABASE neondb TO bookr_analyst_ro;
GRANT USAGE ON SCHEMA public TO bookr_analyst_ro;

-- Only the tables the sync reads. PII columns are dropped/masked by the sync itself,
-- but the role cannot touch anything else (no payments.*, no neon_auth.*, no tokens).
GRANT SELECT ON
  public."User",
  public."ChatSession",
  public."Message",
  public."ExtractedIntent",
  public."UnifiedChatFlowState",
  public."FlightSearchRequest",
  public."FlightSearchResult",
  public."FlightOption",
  public."HotelSearchRequest",
  public."HotelSearchResult",
  public."FlightBooking",
  public."LlmCallLog",
  public."AssistantMessageFeedback"
TO bookr_analyst_ro;

-- Belt and braces: every session is read-only and time-limited.
ALTER ROLE bookr_analyst_ro SET default_transaction_read_only = on;
ALTER ROLE bookr_analyst_ro SET statement_timeout = '120s';

-- Then put the new connection string in .env as SOURCE_DATABASE_URL.
-- Verify:  SELECT has_table_privilege('bookr_analyst_ro', 'public."Booking"', 'SELECT');  -- expect false
