"""Browser name and version advertised by the viewer HTTP request."""
import re

def browser_info(user_agent):
    ua=user_agent[:2048]
    for name,pattern in (
        ("Edge",r"(?:EdgA|EdgiOS|Edg)/([\d.]+)"),
        ("Opera",r"(?:OPR|OPiOS)/([\d.]+)"),
        ("Chrome",r"(?:Chrome|CriOS)/([\d.]+)"),
        ("Firefox",r"(?:Firefox|FxiOS)/([\d.]+)"),
        ("Safari",r"Version/([\d.]+).*Safari/"),
    ):
        match=re.search(pattern,ua)
        if match:return {"browser_name":name,"browser_version":match.group(1)[:40]}
    return {"browser_name":"Unbekannt","browser_version":""}
