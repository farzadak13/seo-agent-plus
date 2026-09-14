-- Runs once, when the data volume is first created.
--
-- The test database exists so integration tests can DROP their tables without
-- touching development data. Keeping them apart is what makes
-- SEO_AGENT_TEST_DSN safe to set.
CREATE DATABASE seoagent_test OWNER seoagent;
