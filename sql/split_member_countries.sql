-- Run after load_member_staging.sql, in the same database and schema.
-- Latest available business-date proxy: Last_Flight_Date, then Enrollment_Date.
-- If both dates tie for a member across countries, the source has no timestamp
-- or sequence to identify the true latest record.
CREATE OR REPLACE TEMPORARY TABLE MEMBER_LATEST AS
SELECT *
FROM MEMBER_STAGING
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY Member_Id
    ORDER BY
        Last_Flight_Date DESC NULLS LAST,
        Enrollment_Date DESC NULLS LAST
) = 1;

-- Keep a registry so a country that becomes empty is still rebuilt as an
-- empty table instead of retaining members who have moved away.
CREATE TABLE IF NOT EXISTS MEMBER_COUNTRY_REGISTRY (
    Country VARCHAR NOT NULL
);

INSERT INTO MEMBER_COUNTRY_REGISTRY (Country)
SELECT DISTINCT s.Country
FROM MEMBER_LATEST AS s
WHERE s.Country IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM MEMBER_COUNTRY_REGISTRY AS r
      WHERE r.Country = s.Country
  );

-- Rebuild one table per current or previously seen country. Country values are
-- bound as values; quoted identifiers allow country names with punctuation.
DECLARE
    country_cursor CURSOR FOR
        SELECT Country
        FROM MEMBER_COUNTRY_REGISTRY;
    country_code VARCHAR;
    target_table VARCHAR;
    create_sql VARCHAR;
BEGIN
    FOR country_row IN country_cursor DO
        country_code := country_row.Country;
        target_table :=
            '"MEMBER_COUNTRY_' || REPLACE(country_code, '"', '""') || '"';
        create_sql :=
            'CREATE OR REPLACE TABLE IDENTIFIER(?) AS '
            || 'SELECT * FROM MEMBER_LATEST WHERE Country = ?';
        EXECUTE IMMEDIATE :create_sql USING (target_table, country_code);
    END FOR;
END;
