import pandas as pd

base = pd.DataFrame({"ID": ["1", "2", "3"], "NAME": ["Asha", "Ravi", "Meera"], "CITY": ["Pune", "Delhi", "Chennai"]})
base.to_csv("sample_data/source.csv", index=False)
base.to_excel("sample_data/target_same.xlsx", index=False)

bad = base.copy()
bad.loc[1, "CITY"] = "Mumbai"
bad = pd.concat([bad, pd.DataFrame([{"ID": "4", "NAME": "Kiran", "CITY": "Goa"}])])
bad.to_excel("sample_data/target_diff.xlsx", index=False)

base.to_xml("sample_data/target_same.xml", index=False, root_name="data", row_name="record")