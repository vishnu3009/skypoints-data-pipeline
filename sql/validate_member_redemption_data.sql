-- Run after MEMBER_STAGING and REDEMPTIONS_RAW have been refreshed.
-- Re-running replaces the report with the current set of validation findings.
CREATE OR REPLACE TABLE DATA_QUALITY_ISSUES AS
WITH
member_total AS (
    SELECT COUNT(*) AS total_count
    FROM MEMBER_STAGING
),
member_id_lengths AS (
    SELECT
        LENGTH(TO_VARCHAR(Member_Id)) AS id_length,
        COUNT(*) AS member_count
    FROM MEMBER_STAGING
    WHERE Member_Id IS NOT NULL
    GROUP BY LENGTH(TO_VARCHAR(Member_Id))
),
common_member_id_length AS (
    SELECT id_length
    FROM member_id_lengths
    QUALIFY ROW_NUMBER() OVER (
        ORDER BY member_count DESC, id_length
    ) = 1
),
member_issues AS (
    SELECT
        'MEMBER_REQUIRED_FIELD' AS issue_type,
        COALESCE(TO_VARCHAR(Member_Id), '<NULL>') AS record_key,
        'Member_Name is null or blank' AS details
    FROM MEMBER_STAGING
    WHERE Member_Name IS NULL OR TRIM(TO_VARCHAR(Member_Name)) = ''

    UNION ALL
    SELECT 'MEMBER_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'Member_Id is null or blank'
    FROM MEMBER_STAGING
    WHERE Member_Id IS NULL OR TRIM(TO_VARCHAR(Member_Id)) = ''

    UNION ALL
    SELECT 'MEMBER_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'Enrollment_Date is null'
    FROM MEMBER_STAGING
    WHERE Enrollment_Date IS NULL

    UNION ALL
    SELECT 'MEMBER_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'Last_Flight_Date is null'
    FROM MEMBER_STAGING
    WHERE Last_Flight_Date IS NULL

    UNION ALL
    SELECT 'MEMBER_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'Tier_Code is null or blank'
    FROM MEMBER_STAGING
    WHERE Tier_Code IS NULL OR TRIM(TO_VARCHAR(Tier_Code)) = ''

    UNION ALL
    SELECT 'MEMBER_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'Agent_Name is null or blank'
    FROM MEMBER_STAGING
    WHERE Agent_Name IS NULL OR TRIM(TO_VARCHAR(Agent_Name)) = ''

    UNION ALL
    SELECT 'MEMBER_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'State is null or blank'
    FROM MEMBER_STAGING
    WHERE State IS NULL OR TRIM(TO_VARCHAR(State)) = ''

    UNION ALL
    SELECT 'MEMBER_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'Country is null or blank'
    FROM MEMBER_STAGING
    WHERE Country IS NULL OR TRIM(TO_VARCHAR(Country)) = ''

    UNION ALL
    SELECT 'MEMBER_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'DOB is null'
    FROM MEMBER_STAGING
    WHERE DOB IS NULL

    UNION ALL
    SELECT 'MEMBER_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'Is_Active is null or blank'
    FROM MEMBER_STAGING
    WHERE Is_Active IS NULL OR TRIM(TO_VARCHAR(Is_Active)) = ''

    UNION ALL
    SELECT 'MEMBER_DUPLICATE_KEY', TO_VARCHAR(Member_Id),
           'Member_Id occurs ' || COUNT(*) || ' times in MEMBER_STAGING'
    FROM MEMBER_STAGING
    WHERE Member_Id IS NOT NULL
    GROUP BY Member_Id
    HAVING COUNT(*) > 1

    UNION ALL
    SELECT 'MEMBER_INVALID_ID', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'Member_Id must contain digits only'
    FROM MEMBER_STAGING
    WHERE Member_Id IS NOT NULL
      AND NOT REGEXP_LIKE(TO_VARCHAR(Member_Id), '^[0-9]+$')

    UNION ALL
    SELECT 'MEMBER_ID_LENGTH_OUTLIER', TO_VARCHAR(m.Member_Id),
           'Member_Id has length ' || LENGTH(TO_VARCHAR(m.Member_Id))
           || '; the most common length is ' || c.id_length
    FROM MEMBER_STAGING AS m
    CROSS JOIN common_member_id_length AS c
    WHERE m.Member_Id IS NOT NULL
      AND LENGTH(TO_VARCHAR(m.Member_Id)) <> c.id_length

    UNION ALL
    SELECT 'COUNTRY_CODE_FORMAT', COALESCE(TO_VARCHAR(Member_Id), '<NULL>'),
           'Country code "' || TO_VARCHAR(Country)
           || '" is not 2 or 3 uppercase letters; validate against a country reference table'
    FROM MEMBER_STAGING
    WHERE Country IS NOT NULL
      AND NOT REGEXP_LIKE(TO_VARCHAR(Country), '^[A-Z]{2,3}$')

    UNION ALL
    SELECT 'MEMBER_DATE_CONCENTRATION',
           'DOB=' || TO_VARCHAR(m.DOB),
           'This DOB occurs for ' || COUNT(*) || ' of ' || t.total_count
           || ' staged rows; investigate unusually uniform sample values'
    FROM MEMBER_STAGING AS m
    CROSS JOIN member_total AS t
    WHERE m.DOB IS NOT NULL
    GROUP BY m.DOB, t.total_count
    HAVING t.total_count > 1
       AND COUNT(*) >= 0.8 * t.total_count

    UNION ALL
    SELECT 'MEMBER_DATE_CONCENTRATION',
           'Enrollment_Date=' || TO_VARCHAR(m.Enrollment_Date),
           'This enrollment date occurs for ' || COUNT(*) || ' of ' || t.total_count
           || ' staged rows; investigate unusually uniform sample values'
    FROM MEMBER_STAGING AS m
    CROSS JOIN member_total AS t
    WHERE m.Enrollment_Date IS NOT NULL
    GROUP BY m.Enrollment_Date, t.total_count
    HAVING t.total_count > 1
       AND COUNT(*) >= 0.8 * t.total_count

    UNION ALL
    SELECT 'MEMBER_DATE_CONCENTRATION',
           'Last_Flight_Date=' || TO_VARCHAR(m.Last_Flight_Date),
           'This last-flight date occurs for ' || COUNT(*) || ' of ' || t.total_count
           || ' staged rows; investigate unusually uniform sample values'
    FROM MEMBER_STAGING AS m
    CROSS JOIN member_total AS t
    WHERE m.Last_Flight_Date IS NOT NULL
    GROUP BY m.Last_Flight_Date, t.total_count
    HAVING t.total_count > 1
       AND COUNT(*) >= 0.8 * t.total_count

    UNION ALL
    SELECT 'MEMBER_DATE_ORDER', TO_VARCHAR(Member_Id),
           'DOB must be earlier than Enrollment_Date'
    FROM MEMBER_STAGING
    WHERE DOB IS NOT NULL
      AND Enrollment_Date IS NOT NULL
      AND DOB >= Enrollment_Date

    UNION ALL
    SELECT 'MEMBER_DATE_ORDER', TO_VARCHAR(Member_Id),
           'Enrollment_Date is in the future'
    FROM MEMBER_STAGING
    WHERE Enrollment_Date > CURRENT_DATE()

    UNION ALL
    SELECT 'MEMBER_DATE_ORDER', TO_VARCHAR(Member_Id),
           'Last_Flight_Date is earlier than Enrollment_Date'
    FROM MEMBER_STAGING
    WHERE Last_Flight_Date IS NOT NULL
      AND Enrollment_Date IS NOT NULL
      AND Last_Flight_Date < Enrollment_Date

    UNION ALL
    SELECT 'MEMBER_AGE_RANGE', TO_VARCHAR(Member_Id),
           'Age must be between 0 and 120'
    FROM MEMBER_STAGING
    WHERE Age IS NOT NULL
      AND (Age < 0 OR Age > 120)

    UNION ALL
    SELECT 'MEMBER_DERIVED_VALUE', TO_VARCHAR(Member_Id),
           'Age is null or does not match DOB and current date'
    FROM MEMBER_STAGING
    WHERE DOB IS NOT NULL
      AND (
          Age IS NULL
          OR Age <> DATEDIFF(year, DOB, CURRENT_DATE())
              - IFF(
                  TO_CHAR(CURRENT_DATE(), 'MMDD') < TO_CHAR(DOB, 'MMDD'),
                  1,
                  0
              )
      )

    UNION ALL
    SELECT 'MEMBER_DERIVED_VALUE', TO_VARCHAR(Member_Id),
           'Stale_Member is null or does not match the 90-day rule'
    FROM MEMBER_STAGING
    WHERE Last_Flight_Date IS NOT NULL
      AND (
          Stale_Member IS NULL
          OR Stale_Member <> (
              DATEDIFF(day, Last_Flight_Date, CURRENT_DATE()) > 90
          )
      )
),
redemption_issues AS (
    SELECT 'REDEMPTION_REQUIRED_FIELD' AS issue_type,
           COALESCE(TO_VARCHAR(Txn_Id), '<NULL>') AS record_key,
           'Member_Id is null or blank' AS details
    FROM REDEMPTIONS_RAW
    WHERE Member_Id IS NULL OR TRIM(TO_VARCHAR(Member_Id)) = ''

    UNION ALL
    SELECT 'REDEMPTION_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Txn_Id), '<NULL>'),
           'Feed_Date is null'
    FROM REDEMPTIONS_RAW
    WHERE Feed_Date IS NULL

    UNION ALL
    SELECT 'REDEMPTION_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Txn_Id), '<NULL>'),
           'Txn_Id is null or blank'
    FROM REDEMPTIONS_RAW
    WHERE Txn_Id IS NULL OR TRIM(TO_VARCHAR(Txn_Id)) = ''

    UNION ALL
    SELECT 'REDEMPTION_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Txn_Id), '<NULL>'),
           'Txn_Date is null'
    FROM REDEMPTIONS_RAW
    WHERE Txn_Date IS NULL

    UNION ALL
    SELECT 'REDEMPTION_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Txn_Id), '<NULL>'),
           'Partner is null or blank'
    FROM REDEMPTIONS_RAW
    WHERE Partner IS NULL OR TRIM(TO_VARCHAR(Partner)) = ''

    UNION ALL
    SELECT 'REDEMPTION_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Txn_Id), '<NULL>'),
           'Miles_Redeemed is null'
    FROM REDEMPTIONS_RAW
    WHERE Miles_Redeemed IS NULL

    UNION ALL
    SELECT 'REDEMPTION_REQUIRED_FIELD', COALESCE(TO_VARCHAR(Txn_Id), '<NULL>'),
           'Status is null or blank'
    FROM REDEMPTIONS_RAW
    WHERE Status IS NULL OR TRIM(TO_VARCHAR(Status)) = ''

    UNION ALL
    SELECT 'REDEMPTION_DUPLICATE_KEY',
           COALESCE(TO_VARCHAR(Member_Id), '<NULL>') || ':' || TO_VARCHAR(Txn_Id),
           'Member_Id and Txn_Id occur ' || COUNT(*) || ' times'
    FROM REDEMPTIONS_RAW
    WHERE Member_Id IS NOT NULL AND Txn_Id IS NOT NULL
    GROUP BY Member_Id, Txn_Id
    HAVING COUNT(*) > 1

    UNION ALL
    SELECT 'REDEMPTION_INVALID_VALUE', TO_VARCHAR(Txn_Id),
           'Miles_Redeemed must be non-negative'
    FROM REDEMPTIONS_RAW
    WHERE Miles_Redeemed < 0

    UNION ALL
    SELECT 'REDEMPTION_DATE_ORDER', TO_VARCHAR(Txn_Id),
           'Txn_Date is later than Feed_Date'
    FROM REDEMPTIONS_RAW
    WHERE Txn_Date IS NOT NULL
      AND Feed_Date IS NOT NULL
      AND Txn_Date > Feed_Date

    UNION ALL
    SELECT 'REDEMPTION_DATE_ORDER', TO_VARCHAR(Txn_Id),
           'Feed_Date is in the future'
    FROM REDEMPTIONS_RAW
    WHERE Feed_Date > CURRENT_DATE()

    UNION ALL
    SELECT 'REDEMPTION_ORPHAN_MEMBER', TO_VARCHAR(r.Txn_Id),
           'No matching Member_Id exists in MEMBER_STAGING'
    FROM REDEMPTIONS_RAW AS r
    WHERE r.Member_Id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM MEMBER_STAGING AS m
          WHERE TO_VARCHAR(m.Member_Id) = TO_VARCHAR(r.Member_Id)
      )
)
SELECT
    CURRENT_TIMESTAMP() AS checked_at,
    IFF(
        issue_type IN (
            'MEMBER_ID_LENGTH_OUTLIER',
            'COUNTRY_CODE_FORMAT',
            'MEMBER_DATE_CONCENTRATION'
        ),
        'WARNING',
        'ERROR'
    ) AS severity,
    issue_type,
    record_key,
    details
FROM member_issues
UNION ALL
SELECT
    CURRENT_TIMESTAMP() AS checked_at,
    IFF(
        issue_type IN (
            'MEMBER_ID_LENGTH_OUTLIER',
            'COUNTRY_CODE_FORMAT',
            'MEMBER_DATE_CONCENTRATION'
        ),
        'WARNING',
        'ERROR'
    ) AS severity,
    issue_type,
    record_key,
    details
FROM redemption_issues;

-- Review all findings. A successful run with zero findings returns no rows.
SELECT severity, issue_type, record_key, details
FROM DATA_QUALITY_ISSUES
ORDER BY severity, issue_type, record_key;
