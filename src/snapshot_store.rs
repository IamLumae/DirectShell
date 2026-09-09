//! Stage a complete UI tree privately, then publish elements and metadata together.
use rusqlite::{Connection, Result};

pub fn prepare(conn: &Connection) -> Result<()> {
    conn.execute_batch("DROP TABLE IF EXISTS temp.snapshot_elements;
        CREATE TEMP TABLE snapshot_elements AS SELECT * FROM elements WHERE 0;")
}

pub fn publish(conn: &Connection, metadata: &[(String, String)]) -> Result<()> {
    let transaction = conn.unchecked_transaction()?;
    transaction.execute_batch("DELETE FROM elements;
        INSERT INTO elements SELECT * FROM temp.snapshot_elements;
        DELETE FROM meta;")?;
    for (key, value) in metadata {
        transaction.execute("INSERT INTO meta(key,value) VALUES(?1,?2)", [key, value])?;
    }
    transaction.commit()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("CREATE TABLE elements(id INTEGER PRIMARY KEY, name TEXT);
            CREATE INDEX idx_name ON elements(name);
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO elements VALUES(1,'old'); INSERT INTO meta VALUES('generation','old');").unwrap();
        conn
    }
    #[test]
    fn building_a_snapshot_keeps_old_generation_and_indexes_visible() {
        let conn=fixture(); prepare(&conn).unwrap();
        conn.execute("INSERT INTO temp.snapshot_elements VALUES(2,'new')", []).unwrap();
        assert_eq!(conn.query_row("SELECT name FROM elements", [], |r| r.get::<_,String>(0)).unwrap(), "old");
        publish(&conn, &[("generation".into(),"new".into())]).unwrap();
        assert_eq!(conn.query_row("SELECT name FROM elements", [], |r| r.get::<_,String>(0)).unwrap(), "new");
        assert_eq!(conn.query_row("SELECT value FROM meta WHERE key='generation'", [], |r| r.get::<_,String>(0)).unwrap(), "new");
        assert_eq!(conn.query_row("SELECT count(*) FROM sqlite_master WHERE name='idx_name'", [], |r| r.get::<_,i64>(0)).unwrap(), 1);
    }
    #[test]
    fn failed_publication_rolls_back_elements_and_metadata_together() {
        let conn=fixture(); prepare(&conn).unwrap();
        conn.execute_batch("INSERT INTO temp.snapshot_elements VALUES(2,'a'),(2,'duplicate');").unwrap();
        assert!(publish(&conn, &[("generation".into(),"bad".into())]).is_err());
        assert_eq!(conn.query_row("SELECT name FROM elements", [], |r| r.get::<_,String>(0)).unwrap(), "old");
        assert_eq!(conn.query_row("SELECT value FROM meta WHERE key='generation'", [], |r| r.get::<_,String>(0)).unwrap(), "old");
    }
}
