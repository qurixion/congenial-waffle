# congenial-waffle

A Google Maps lead generation tool that extracts business information and scrapes contact details from websites.

## What It Does

This tool searches Google Maps for businesses in specified locations and categories. It collects business details like name, phone, address, website, and ratings. Then it visits each business website to find email addresses and social media links. Everything is saved to CSV, Excel, and JSON files.

## Features

- Search by category and location
- Automatic GPS coordinate lookup from location names
- Extract emails from business websites
- Find social media profiles (Facebook, Instagram, Twitter, LinkedIn, YouTube, TikTok)
- Duplicate detection across multiple runs
- Auto-save progress every 30 leads
- Export to CSV, Excel, and JSON

## Requirements

- Python 3.7 or higher
- Chrome or Chromium browser
- ChromeDriver

## Installation

Clone the repository:

```bash
git clone https://github.com/yourusername/congenial-waffle.git
cd congenial-waffle
```

Install Python packages:

```bash
pip install selenium webdriver-manager beautifulsoup4 requests pandas openpyxl
```

Install Chrome on Fedora:

```bash
sudo dnf install chromium chromium-headless chromedriver
```

Install Chrome on Ubuntu/Debian:

```bash
sudo apt install chromium-browser chromium-chromedriver
```

## Usage

There are two ways to use this tool: interactive mode and command line mode.

### Interactive Mode

Run without any arguments and follow the prompts:

```bash
python lead_generator.py
```

The script will ask you for:
- Search mode (GPS or text-based)
- Categories to search
- Regions/locations to search
- Number of leads per search
- Output file path

### Command Line Mode

Run with flags for automated usage:

```bash
python lead_generator.py -c "restaurant" -r "Paris France" -o leads.csv
```

### Command Line Flags

| Flag     | Description                        | Example                     |
|----------|------------------------------------|-----------------------------|
| -c       | Categories to search               | -c "bar,cafe,restaurant"    |
| -r       | Regions to search                  | -r "Paris France,London UK" |
| -o       | Output file or directory           | -o leads.csv                |
| -l       | Leads per search (default 20)      | -l 50                       |
| --no-gps | Use text search instead of GPS     | --no-gps                    |

## How Location Search Works

When you enter a location name, the script automatically finds its GPS coordinates using OpenStreetMap:

| You Enter | Script Finds |
|-----------|--------------|
| Paris France | 48.8566, 2.3522 |
| Tokyo Japan | 35.6828, 139.7594 |
| New York NY | 40.7128, -74.0060 |

The script then searches Google Maps at those exact coordinates. This ensures you get businesses actually located there, not businesses that just have the city name in their title.

Use --no-gps flag for faster but less accurate text-based search.

## Output

The script creates three files:
- leads.csv - For Excel and Google Sheets
- leads.xlsx - Excel format
- leads.json - For programming use

If output file already exists, new leads are added without duplicates.

## Data Collected

For each business, the script collects:

| Field | Description |
|-------|-------------|
| name | Business name |
| phone | Phone number |
| address | Full address |
| website | Website URL |
| rating | Google rating (1-5) |
| reviews | Number of reviews |
| category | Business type |
| emails | Emails found on website |
| facebook | Facebook page |
| instagram | Instagram profile |
| twitter | Twitter/X profile |
| linkedin | LinkedIn page |
| youtube | YouTube channel |
| tiktok | TikTok profile |

## Examples

Collect 50 restaurants in London:

```bash
python lead_generator.py -c "restaurant" -r "London UK" -o london.csv -l 50
```

Multiple categories and regions:

```bash
python lead_generator.py -c "bar,cafe" -r "Paris,Lyon,Marseille" -o france.csv
```

Using text files:

```bash
python lead_generator.py -c categories.txt -r regions.txt -o leads.csv
```

## Using Text Files

Create text files with one item per line.

categories.txt:

```
restaurant
cafe
bar
hotel
```

regions.txt:

```
Paris France
London UK
Berlin Germany
Tokyo Japan
```

## Troubleshooting

**Browser fails to start:**
Make sure Chrome/Chromium and ChromeDriver are installed.

**Geocoding fails:**
Try adding the country name like "Paris France" instead of just "Paris".

**No results found:**
Try different category names or larger cities.

**Script interrupted:**
Check the output file for partial results. The script auto-saves every 30 leads.

## Notes

- The script includes delays to avoid being blocked
- Geocoding uses free OpenStreetMap service
- Errors are logged to lead_generation.log

## License

MIT License
