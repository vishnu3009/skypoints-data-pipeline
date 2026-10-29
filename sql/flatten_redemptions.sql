-- Run after code/load_redemptions.py has loaded the JSON transactions into
-- REDEMPTIONS_RAW and after MEMBER_STAGING has been refreshed.
--
-- The Python loader parses each JSON redemption object into one row in
-- REDEMPTIONS_RAW. This transformation produces a queryable transaction table
-- and attaches the latest available member profile from MEMBER_STAGING.
CREATE OR REPLACE TABLE REDEMPTIONS_FLAT AS
WITH member_profile AS (
    SELECT
        Member_Id,
        Member_Name,
        Enrollment_Date,
        Last_Flight_Date,
        Tier_Code,
        Agent_Name,
        State,
        Country,
        DOB,
        Is_Active,
        Age,
        Stale_Member
    FROM MEMBER_STAGING
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY Member_Id
        ORDER BY
            Last_Flight_Date DESC NULLS LAST,
            Enrollment_Date DESC NULLS LAST
    ) = 1
)
SELECT
    r.Member_Id,
    r.Feed_Date,
    r.Txn_Id,
    r.Txn_Date,
    r.Partner,
    r.Miles_Redeemed,
    r.Status,
    m.Member_Name,
    m.Tier_Code,
    m.Country,
    m.State,
    m.Agent_Name,
    m.Enrollment_Date,
    m.Last_Flight_Date,
    m.DOB,
    m.Age,
    m.Stale_Member,
    m.Is_Active,
    IFF(m.Member_Id IS NULL, FALSE, TRUE) AS Member_Profile_Found
FROM REDEMPTIONS_RAW AS r
LEFT JOIN member_profile AS m
    ON TO_VARCHAR(r.Member_Id) = TO_VARCHAR(m.Member_Id);
