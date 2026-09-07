-- New isolated deployment only. Explicit confirmation + tested backup required.
-- Applied once through reviewed migration history; never mounted into DB init.
BEGIN;
CREATE ROLE macserver_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS;
GRANT macserver_owner TO postgres;
CREATE SCHEMA api AUTHORIZATION macserver_owner;
CREATE SCHEMA app_private AUTHORIZATION macserver_owner;
REVOKE ALL ON SCHEMA api, app_private FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA api TO authenticated, service_role;
GRANT USAGE ON SCHEMA auth TO macserver_owner;
GRANT EXECUTE ON FUNCTION auth.uid() TO macserver_owner;
-- Global to this new, application-only owner: schema-local REVOKE cannot cancel
-- PostgreSQL's default global PUBLIC function EXECUTE grant.
ALTER DEFAULT PRIVILEGES FOR ROLE macserver_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE macserver_owner REVOKE ALL ON TABLES FROM PUBLIC, anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES FOR ROLE macserver_owner REVOKE ALL ON SEQUENCES FROM PUBLIC, anon, authenticated, service_role;
-- Future migrations create objects as macserver_owner, enable RLS, then grant only
-- required actions. There are deliberately no application tables or broad grants.
COMMIT;
