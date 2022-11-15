# TRT Tiger ReTail

an online web platform for Princeton students to buy/sell/request goods

# Running locally

## Initial setup

1. Create a new conda environment: `conda create -n tigerretail`
1. Activate the conda environment: `conda activate tigerretail`
1. Install python: `conda install python=3.10`
1. `cd` into the base TigerReTail directory
1. Install dependencies: `pip install -r requirements.txt`
1. Set all environment variables:
   - Login to Heroku and go to the Settings tab for the `trt-dev` app (do NOT use prod!)
   - Reveal Config Vars
   - For each Config Var key-value pair, create a local environment variable: `conda env config vars set key=value` (replace `key` and `value` with the actual key and value)
   - Note that for `SECRET_KEY`, you might get an error so you can set its value to `1`

## Running the dev server

After following the initial setup steps above, you can run the local development server:

1. `cd` into the `TRT-django` directory (a subfolder of the base directory)
1. Activate your environment: `conda activate tigerretail`
1. Run the server: `python manage.py runserver`
