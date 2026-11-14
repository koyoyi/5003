SELECT UserID, toMinute(EventTime) AS m, SearchPhrase, COUNT(*) FROM hits GROUP BY UserID, m, SearchPhrase ORDER BY COUNT(*) DESC, UserID, m, SearchPhrase LIMIT 10;
