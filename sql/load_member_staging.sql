-- Run in the database and schema containing MEMBER_RAW.
-- This rebuilds MEMBER_STAGING from the raw member table.
CREATE OR REPLACE TABLE MEMBER_STAGING AS
SELECT
    m.*,
    DATEDIFF(year, m.DOB, CURRENT_DATE())
        - IFF(
            TO_CHAR(CURRENT_DATE(), 'MMDD') < TO_CHAR(m.DOB, 'MMDD'),
            1,
            0
        ) AS AGE,
    DATEDIFF(day, m.Last_Flight_Date, CURRENT_DATE()) > 90 AS STALE_MEMBER
FROM MEMBER_RAW AS m;
