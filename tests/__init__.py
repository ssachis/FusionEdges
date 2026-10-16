from fusionedges import llm

llm._sleep = lambda seconds: None  # tests never really wait out rate limits
